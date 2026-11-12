import argparse
import json
from pathlib import Path
import sys
import tempfile
from network.contracts import Study, canonical
from network import interchange
from network.cache import RouteCache
from analysis_runs.pipeline import execute, audit
from analysis_runs.workspace import Workspace
from analysis_runs import recovery, package
from analysis_runs.reports import report
from equity.comparison import compare, pair_transitions


def read(path):
    source = Path(path)
    if source.is_symlink() or not source.is_file() or source.stat().st_size > 50_000_000:
        raise ValueError('input must be bounded regular file')
    return json.loads(source.read_text(encoding='utf-8'))


def demo(path):
    study = Study(read(path))
    with tempfile.TemporaryDirectory(prefix='access-demo-') as directory:
        root = Path(directory)
        with RouteCache(root / 'routes.sqlite3') as cache:
            first = cache.prewarm(study)
            second = cache.prewarm(study)
            if not all(row['cached'] for row in second['routes']):
                raise ValueError('route cache reuse failed')
            route_audit = cache.audit()
        with Workspace(root / 'workspace.sqlite3') as workspace:
            workspace.create('baseline', study)
            result = workspace.analyze('baseline', 1)
            checked = audit(study, result['result'], replay=True)
            workspace.publish('v1', result['key'])
            bundle = package.write(study, result['result'], root / 'package')
            interchange.write_directory(study, root / 'tables')
            if interchange.read_directory(root / 'tables').fingerprint != study.fingerprint:
                raise ValueError('table roundtrip mismatch')
            saved = recovery.backup(workspace, root / 'backup.sqlite3')
            restored = recovery.restore(root / 'backup.sqlite3', root / 'restored.sqlite3', saved['sha256'])
            return {
                'overall': result['result']['overall'],
                'pipeline_audit': checked,
                'route_cache_audit': route_audit,
                'package': bundle,
                'restored': restored,
                'source': 'local_synthetic_fixture',
                'runtime_downloads': False,
            }


def parser():
    root = argparse.ArgumentParser(description='Static community service accessibility analysis')
    commands = root.add_subparsers(dest='command', required=True)
    demonstration = commands.add_parser('demo')
    demonstration.add_argument('--fixture', required=True)
    analyze = commands.add_parser('analyze')
    analyze.add_argument('--input', required=True)
    analyze.add_argument('--direction', choices=['outbound', 'return', 'roundtrip'], default='outbound')
    analyze.add_argument('--equal-priority', action='store_true')
    create = commands.add_parser('create')
    create.add_argument('--database', required=True)
    create.add_argument('--branch', required=True)
    create.add_argument('--input', required=True)
    for action in ('fork', 'revise'):
        command = commands.add_parser(action)
        command.add_argument('--database', required=True)
        command.add_argument('--branch', required=True)
        command.add_argument('--revision', required=True, type=int)
        command.add_argument('--patches', required=True)
        if action == 'fork':
            command.add_argument('--target', required=True)
    run = commands.add_parser('run')
    run.add_argument('--database', required=True)
    run.add_argument('--branch', required=True)
    run.add_argument('--revision', required=True, type=int)
    run.add_argument('--direction', choices=['outbound', 'return', 'roundtrip'], default='outbound')
    run.add_argument('--equal-priority', action='store_true')
    reporting = commands.add_parser('report')
    reporting.add_argument('--database', required=True)
    reporting.add_argument('--key', required=True)
    reporting.add_argument('--kind', required=True)
    reporting.add_argument('--options')
    comparison = commands.add_parser('compare')
    comparison.add_argument('--database', required=True)
    comparison.add_argument('--before', required=True)
    comparison.add_argument('--after', required=True)
    publish = commands.add_parser('publish')
    publish.add_argument('--database', required=True)
    publish.add_argument('--key', required=True)
    publish.add_argument('--label', required=True)
    publish.add_argument('--minimum-communities', type=int, default=3)
    publish.add_argument('--minimum-demand', type=int, default=10)
    export = commands.add_parser('export')
    export.add_argument('--database', required=True)
    export.add_argument('--key', required=True)
    export.add_argument('--directory', required=True)
    verification = commands.add_parser('verify-package')
    verification.add_argument('--directory', required=True)
    for action in ('csv-in', 'csv-out'):
        command = commands.add_parser(action)
        command.add_argument('--input', required=True)
        command.add_argument('--output', required=True)
    audit_command = commands.add_parser('audit')
    audit_command.add_argument('--database', required=True)
    backup = commands.add_parser('backup')
    backup.add_argument('--database', required=True)
    backup.add_argument('--output', required=True)
    restore = commands.add_parser('restore')
    restore.add_argument('--input', required=True)
    restore.add_argument('--output', required=True)
    restore.add_argument('--sha256', required=True)
    routes = commands.add_parser('cache')
    routes.add_argument('--input', required=True)
    routes.add_argument('--database', required=True)
    routes.add_argument('--audit', action='store_true')
    api = commands.add_parser('serve')
    api.add_argument('--database', required=True)
    api.add_argument('--token-file', required=True)
    api.add_argument('--port', type=int, default=8080)
    correction = commands.add_parser('correct')
    correction.add_argument('--input', required=True)
    correction.add_argument('--operations', required=True)
    correction.add_argument('--output', required=True)
    return root


def action(args):
    if args.command == 'correct':
        from network.corrections import apply
        revised, evidence = apply(Study(read(args.input)), read(args.operations))
        with Path(args.output).open('x', encoding='utf-8') as output:
            output.write(canonical(revised.document))
        return evidence
    if args.command == 'serve':
        from analysis_runs.http_api import serve
        token_path = Path(args.token_file)
        if token_path.is_symlink() or token_path.stat().st_mode & 0o077:
            raise ValueError('token file must be regular and owner-only')
        serve(args.database, token_path.read_text().strip(), args.port)
        return {'stopped': True}
    if args.command == 'demo':
        return demo(args.fixture)
    if args.command == 'analyze':
        return execute(Study(read(args.input)), args.direction, not args.equal_priority)
    if args.command == 'verify-package':
        return package.verify(args.directory)
    if args.command == 'csv-out':
        return interchange.write_directory(Study(read(args.input)), args.output)
    if args.command == 'csv-in':
        study = interchange.read_directory(args.input)
        target = Path(args.output)
        with target.open('x', encoding='utf-8') as output:
            output.write(canonical(study.document))
        return {'input_digest': study.fingerprint, 'path': str(target)}
    if args.command == 'restore':
        return recovery.restore(args.input, args.output, args.sha256)
    if args.command == 'cache':
        with RouteCache(args.database) as cache:
            result = cache.prewarm(Study(read(args.input)))
            if args.audit:
                result['audit'] = cache.audit()
            result['statistics'] = cache.statistics()
            return result
    with Workspace(args.database) as workspace:
        if args.command == 'create':
            return workspace.create(args.branch, Study(read(args.input)))
        if args.command == 'fork':
            return workspace.fork(args.branch, args.target, args.revision, read(args.patches))
        if args.command == 'revise':
            return workspace.revise(args.branch, args.revision, read(args.patches))
        if args.command == 'run':
            return workspace.analyze(args.branch, args.revision, args.direction, not args.equal_priority)
        if args.command == 'report':
            result = workspace.result(args.key)
            return report(workspace.study(result['input_digest']), result, args.kind, read(args.options) if args.options else {})
        if args.command == 'compare':
            return {'communities': compare(workspace.result(args.before), workspace.result(args.after)), 'pairs': pair_transitions(workspace.result(args.before), workspace.result(args.after))}
        if args.command == 'publish':
            return workspace.publish(args.label, args.key, args.minimum_communities, args.minimum_demand)
        if args.command == 'export':
            result = workspace.result(args.key)
            return package.write(workspace.study(result['input_digest']), result, args.directory)
        if args.command == 'audit':
            return recovery.audit(workspace)
        if args.command == 'backup':
            return recovery.backup(workspace, args.output)
    raise ValueError('unsupported command')


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = action(args)
    except (ValueError, KeyError, OSError) as exc:
        print(canonical({'error': str(exc)}), file=sys.stderr)
        return 2
    print(canonical(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
