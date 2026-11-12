import copy
import csv
import http.client
from http.server import HTTPServer
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from network.contracts import Study, checksum, canonical
from network.interchange import SCHEMA, read_table, write_table, read_directory, write_directory, replace_tables, parse_cell
from analysis_runs.workspace import Workspace, RevisionConflict
from analysis_runs import recovery, package
from analysis_runs.scenarios import pool_capacity_patches
from analysis_runs.http_api import handler, dispatch
from accessibility import demo, main


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.study = Study(json.loads(Path('fixtures/community.json').read_text()))
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / 'workspace.sqlite3'
        self.workspace = Workspace(self.path)
        self.workspace.create('base', self.study)

    def tearDown(self):
        self.workspace.close()
        self.temp.cleanup()

    def test_branch_immutable_creation_and_analysis_cache(self):
        branch = self.workspace.branch('base')
        self.assertEqual(branch['revision'], 1)
        self.assertEqual(branch['study'], self.study.fingerprint)
        self.assertEqual(self.workspace.create('base', self.study), branch)
        first = self.workspace.analyze('base', 1)
        second = self.workspace.analyze('base', 1)
        self.assertFalse(first['reused'])
        self.assertTrue(second['reused'])
        self.assertEqual(first['key'], second['key'])
        self.assertEqual(first['result'], second['result'])
        other = self.workspace.analyze('base', 1, 'roundtrip')
        self.assertNotEqual(first['key'], other['key'])
        equal = self.workspace.analyze('base', 1, priorities=False)
        self.assertNotEqual(first['key'], equal['key'])
        self.assertTrue(recovery.audit(self.workspace)['valid'])
        self.assertEqual(recovery.audit(self.workspace)['results'], 3)
        with self.assertRaises(RevisionConflict):
            self.workspace.analyze('base', 2)
        with self.assertRaises(KeyError):
            self.workspace.result('missing')

    def test_revision_conflicts_and_fork_isolation(self):
        patches = pool_capacity_patches(self.study, 'hub', 15)
        fork = self.workspace.fork('base', 'expanded', 1, patches)
        self.assertEqual(fork['revision'], 1)
        self.assertNotEqual(fork['study'], self.study.fingerprint)
        self.assertEqual(self.workspace.branch('base')['study'], self.study.fingerprint)
        with self.assertRaises(RevisionConflict):
            self.workspace.fork('base', 'expanded', 1, patches)
        with self.assertRaises(RevisionConflict):
            self.workspace.fork('base', 'stale', 2, patches)
        second = Workspace(self.path)
        try:
            old = second.branch('base')
            revised = self.workspace.revise('base', 1, patches)
            self.assertEqual(revised['revision'], 2)
            self.assertEqual(revised['study'], fork['study'])
            with self.assertRaises(RevisionConflict):
                second.revise('base', old['revision'], [])
            self.assertEqual(second.branch('base')['revision'], 2)
        finally:
            second.close()
        self.assertTrue(recovery.audit(self.workspace)['valid'])
        self.assertEqual(recovery.audit(self.workspace)['studies'], 2)
        self.assertEqual(recovery.audit(self.workspace)['branches'], 2)

    def test_joint_invalid_revision_rolls_back(self):
        head = self.workspace.branch('base')
        with self.assertRaises(ValueError):
            self.workspace.revise('base', 1, [{'table': 'facilities', 'id': 'elder-hub', 'field': 'capacity', 'value': 99}])
        self.assertEqual(self.workspace.branch('base'), head)
        self.assertEqual(self.workspace.db.execute('SELECT COUNT(*) FROM studies').fetchone()[0], 1)
        self.assertEqual(self.workspace.db.execute('SELECT COUNT(*) FROM branch_history').fetchone()[0], 1)
        result = self.workspace.analyze('base', 1)
        original = result['result']
        self.workspace.revise('base', 1, pool_capacity_patches(self.study, 'hub', 20))
        self.assertEqual(self.workspace.result(result['key']), original)
        changed = self.workspace.analyze('base', 2)
        self.assertNotEqual(changed['key'], result['key'])
        self.assertTrue(recovery.audit(self.workspace)['valid'])

    def test_publication_freeze(self):
        result = self.workspace.analyze('base', 1)
        first = self.workspace.publish('public-v1', result['key'])
        self.assertEqual(self.workspace.publish('public-v1', result['key']), first)
        with self.assertRaises(RevisionConflict):
            self.workspace.publish('public-v1', result['key'], 1, 1)
        self.workspace.revise('base', 1, pool_capacity_patches(self.study, 'hub', 20))
        new = self.workspace.analyze('base', 2)
        with self.assertRaises(RevisionConflict):
            self.workspace.publish('public-v1', new['key'])
        self.assertEqual(self.workspace.publication('public-v1'), first)
        self.assertEqual(first['packet']['result_digest'], result['result']['result_digest'])
        self.assertFalse(first['packet']['contains_routes_or_community_ids'])
        self.assertEqual(recovery.audit(self.workspace)['publications'], 1)
        with self.assertRaises(KeyError):
            self.workspace.publication('missing')

    def test_workspace_backup_and_restore(self):
        result = self.workspace.analyze('base', 1)
        self.workspace.publish('public', result['key'])
        path = self.root / 'backup.sqlite3'
        evidence = recovery.backup(self.workspace, path)
        target = self.root / 'restored.sqlite3'
        restored = recovery.restore(path, target, evidence['sha256'])
        self.assertTrue(restored['restored'])
        self.assertEqual(restored['audit']['results'], 1)
        with Workspace(target) as output:
            self.assertEqual(output.result(result['key']), result['result'])
            self.assertEqual(output.publication('public'), self.workspace.publication('public'))
        with self.assertRaises(FileExistsError):
            recovery.backup(self.workspace, path)
        with self.assertRaises(FileExistsError):
            recovery.restore(path, target, evidence['sha256'])
        with self.assertRaises(ValueError):
            recovery.restore(path, self.root / 'bad.sqlite3', '0' * 64)
        self.assertFalse((self.root / 'bad.sqlite3').exists())

    def test_history_and_result_corruption_detection(self):
        self.workspace.revise('base', 1, pool_capacity_patches(self.study, 'hub', 15))
        self.workspace.db.execute("UPDATE branch_history SET body='{}' WHERE name='base' AND revision=2")
        with self.assertRaises((ValueError, KeyError)):
            recovery.audit(self.workspace)
        self.workspace.db.execute("DELETE FROM branch_history WHERE name='base' AND revision=2")
        with self.assertRaises(ValueError):
            recovery.audit(self.workspace)
        result = self.workspace.analyze('base', 2)
        self.workspace.db.execute("UPDATE results SET digest=? WHERE key=?", ('0' * 64, result['key']))
        with self.assertRaises(ValueError):
            self.workspace.result(result['key'])

    def test_all_csv_tables_roundtrip(self):
        for table in SCHEMA:
            with self.subTest(table=table):
                content = write_table(self.study, table)
                actual = read_table(table, content)
                self.assertEqual(actual, self.study.document[table])
                self.assertEqual(next(csv.reader(io.StringIO(content))), [key for key, kind in SCHEMA[table]])
        target = self.root / 'tables'
        evidence = write_directory(self.study, target)
        self.assertEqual(len(evidence['files']), 5)
        restored = read_directory(target)
        self.assertEqual(restored.fingerprint, self.study.fingerprint)
        self.assertEqual(restored.document, self.study.document)
        with self.assertRaises(FileExistsError):
            write_directory(self.study, target)
        replaced = replace_tables(self.study, {'nodes': write_table(self.study, 'nodes')})
        self.assertEqual(replaced.fingerprint, self.study.fingerprint)

    def test_csv_bad_headers_width_types_and_links(self):
        content = write_table(self.study, 'nodes')
        with self.assertRaises(ValueError):
            read_table('nodes', content.replace('rural', 'unknown'))
        with self.assertRaises(ValueError):
            read_table('nodes', content + 'wrong,width\n')
        with self.assertRaises(ValueError):
            read_table('nodes', 'id,id,rural\na,a,true\n')
        with self.assertRaises(ValueError):
            parse_cell('1.0', int)
        with self.assertRaises(ValueError):
            parse_cell('１', int)
        with self.assertRaises(ValueError):
            parse_cell('TRUE', bool)
        with self.assertRaises(ValueError):
            parse_cell('{}', list)
        target = self.root / 'tables'
        write_directory(self.study, target)
        extra = target / 'extra'
        extra.write_text('extra')
        with self.assertRaises(ValueError):
            read_directory(target)
        extra.unlink()
        alias = self.root / 'alias'
        alias.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            read_directory(alias)
        with self.assertRaises(ValueError):
            replace_tables(self.study, {})

    def test_package_replay_and_tampering(self):
        result = self.workspace.analyze('base', 1)['result']
        target = self.root / 'package'
        evidence = package.write(self.study, result, target)
        self.assertTrue(evidence['valid'])
        self.assertEqual(evidence['files'], 9)
        self.assertEqual(package.verify(target), evidence)
        with self.assertRaises(FileExistsError):
            package.write(self.study, result, target)
        audit_path = target / 'audit.json'
        original = audit_path.read_text()
        audit_path.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            package.verify(target)
        import hashlib
        manifest_path = target / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['files']['audit.json'] = hashlib.sha256(audit_path.read_bytes()).hexdigest()
        manifest_path.write_text(canonical(manifest))
        with self.assertRaisesRegex(ValueError, 'replay'):
            package.verify(target)
        audit_path.write_text(original)
        manifest['files']['audit.json'] = hashlib.sha256(audit_path.read_bytes()).hexdigest()
        manifest_path.write_text(canonical(manifest))
        self.assertTrue(package.verify(target)['valid'])
        (target / 'extra').write_text('unexpected')
        with self.assertRaises(ValueError):
            package.verify(target)

    def test_read_api_auth_routes_and_reports(self):
        result = self.workspace.analyze('base', 1)
        token = 'test-api-token-longer-than-24-chars'
        server = HTTPServer(('127.0.0.1', 0), handler(self.path, token))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(route, body, auth=True, content_type='application/json'):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            headers = {'Content-Type': content_type}
            if auth:
                headers['Authorization'] = 'Bearer ' + token
            connection.request('POST', route, json.dumps(body), headers)
            response = connection.getresponse()
            output = response.status, json.loads(response.read())
            connection.close()
            return output
        try:
            status, body = request('/read/branch', {'name': 'base'})
            self.assertEqual(status, 200)
            self.assertEqual(body['result']['revision'], 1)
            status, body = request('/read/report', {'key': result['key'], 'kind': 'groups', 'options': {'dimensions': ['region']}})
            self.assertEqual(status, 200)
            self.assertEqual(len(body['result']['rows']), 2)
            self.assertEqual(request('/read/branch', {'name': 'base'}, auth=False)[0], 401)
            self.assertEqual(request('/read/branch', {'name': 'missing'})[0], 404)
            self.assertEqual(request('/wrong', {})[0], 404)
            self.assertEqual(request('/read/branch', {}, content_type='text/plain')[0], 415)
            self.assertEqual(request('/read/unknown', {})[0], 400)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)
        collections = dispatch(self.workspace, 'collections', {})
        self.assertEqual(collections['branches'], ['base'])
        self.assertEqual(collections['results'], [result['key']])
        self.assertEqual(dispatch(self.workspace, 'result', {'key': result['key']}), result['result'])
        self.assertEqual(dispatch(self.workspace, 'compare', {'before': result['key'], 'after': result['key']})['unmatched'], [])
        with self.assertRaises(ValueError):
            handler(self.path, 'short')

    def test_analysis_does_not_commit_when_branch_changes_during_computation(self):
        from unittest.mock import patch
        from analysis_runs.pipeline import execute
        second = Workspace(self.path)
        def concurrent_edit(study, direction, priorities):
            result = execute(study, direction, priorities)
            second.revise('base', 1, pool_capacity_patches(self.study, 'hub', 12))
            return result
        try:
            with patch('analysis_runs.workspace.execute', side_effect=concurrent_edit):
                with self.assertRaises(RevisionConflict):
                    self.workspace.analyze('base', 1)
            self.assertEqual(self.workspace.db.execute('SELECT COUNT(*) FROM results').fetchone()[0], 0)
            self.assertEqual(self.workspace.branch('base')['revision'], 2)
            fresh = self.workspace.analyze('base', 2)
            self.assertEqual(fresh['result']['input_digest'], self.workspace.branch('base')['study'])
            self.assertTrue(recovery.audit(self.workspace)['valid'])
        finally:
            second.close()

    def test_cli_real_archive_and_exports(self):
        from contextlib import redirect_stdout
        def invoke(arguments):
            output = io.StringIO()
            with redirect_stdout(output):
                status = main(arguments)
            self.assertEqual(status, 0)
            return json.loads(output.getvalue())
        database = self.root / 'cli.sqlite3'
        created = invoke(['create', '--database', str(database), '--branch', 'main', '--input', 'fixtures/community.json'])
        self.assertEqual(created['revision'], 1)
        executed = invoke(['run', '--database', str(database), '--branch', 'main', '--revision', '1'])
        self.assertIn('key', executed)
        key = executed['key']
        published = invoke(['publish', '--database', str(database), '--key', key, '--label', 'v1'])
        self.assertEqual(published['label'], 'v1')
        reported = invoke(['report', '--database', str(database), '--key', key, '--kind', 'audit'])
        self.assertTrue(reported['valid'])
        exported = invoke(['export', '--database', str(database), '--key', key, '--directory', str(self.root / 'cli-package')])
        self.assertTrue(exported['valid'])
        checked = invoke(['verify-package', '--directory', str(self.root / 'cli-package')])
        self.assertEqual(checked, exported)
        backed = invoke(['backup', '--database', str(database), '--output', str(self.root / 'cli-backup.sqlite3')])
        restored = invoke(['restore', '--input', str(self.root / 'cli-backup.sqlite3'), '--output', str(self.root / 'cli-restored.sqlite3'), '--sha256', backed['sha256']])
        self.assertTrue(restored['restored'])
        tables = invoke(['csv-out', '--input', 'fixtures/community.json', '--output', str(self.root / 'cli-tables')])
        self.assertEqual(len(tables['files']), 5)
        imported = invoke(['csv-in', '--input', str(self.root / 'cli-tables'), '--output', str(self.root / 'cli-study.json')])
        self.assertEqual(imported['input_digest'], self.study.fingerprint)
        self.assertEqual(json.loads((self.root / 'cli-study.json').read_text()), self.study.document)

    def test_joint_graph_correction_and_reference_delete(self):
        from network.corrections import apply, differences
        additions = [
            {'table': 'nodes', 'id': 'G', 'action': 'add', 'record': {'id': 'G', 'region': 'west', 'rural': True}},
            {'table': 'edges', 'id': 'FG', 'action': 'add', 'record': {'id': 'FG', 'from': 'F', 'to': 'G', 'metres': 50, 'delay_seconds': 0, 'steps': 0, 'slope_permille': 0, 'closed': False, 'profiles': ['walk']}},
        ]
        revised, evidence = apply(self.study, additions)
        self.assertIn('G', revised.nodes)
        self.assertIn('FG', revised.edges)
        self.assertEqual(len(evidence['changes']), 2)
        with self.assertRaises(ValueError):
            apply(revised, [{'table': 'nodes', 'id': 'G', 'action': 'remove'}])
        restored, evidence = apply(revised, [{'table': 'nodes', 'id': 'G', 'action': 'remove'}, {'table': 'edges', 'id': 'FG', 'action': 'remove'}])
        self.assertEqual(restored.fingerprint, self.study.fingerprint)
        self.assertEqual(len(differences(revised, restored)['operations']), 2)
        record = copy.deepcopy(self.study.document['nodes'][0])
        record['id'] = 'different'
        with self.assertRaises(ValueError):
            apply(self.study, [{'table': 'nodes', 'id': 'A', 'action': 'replace', 'record': record}])
        with self.assertRaises(ValueError):
            apply(self.study, [{'table': 'edges', 'id': 'AB', 'action': 'remove', 'record': {}}])

    def test_study_source_corruption_is_not_silently_reused(self):
        result = self.workspace.analyze('base', 1)
        self.workspace.db.execute("UPDATE studies SET body='{}' WHERE digest=?", (self.study.fingerprint,))
        with self.assertRaises(ValueError):
            self.workspace.study(self.study.fingerprint)
        with self.assertRaises(ValueError):
            self.workspace.result(result['key'])
        with self.assertRaises(ValueError):
            recovery.backup(self.workspace, self.root / 'corrupt.sqlite3')
        self.assertFalse((self.root / 'corrupt.sqlite3').exists())
        with self.assertRaises(ValueError):
            self.workspace.create('another', self.study)
        self.assertIsNone(self.workspace.db.execute("SELECT name FROM branches WHERE name='another'").fetchone())
        self.workspace.db.execute('UPDATE studies SET body=? WHERE digest=?', (canonical(self.study.document), self.study.fingerprint))
        self.assertTrue(recovery.audit(self.workspace)['valid'])

    def test_cli_demo_and_failure_output(self):
        result = demo('fixtures/community.json')
        self.assertTrue(result['pipeline_audit']['valid'])
        self.assertTrue(result['route_cache_audit']['valid'])
        self.assertTrue(result['package']['valid'])
        self.assertTrue(result['restored']['restored'])
        self.assertEqual(result['source'], 'local_synthetic_fixture')
        self.assertFalse(result['runtime_downloads'])
        from contextlib import redirect_stderr
        output = io.StringIO()
        with redirect_stderr(output):
            status = main(['analyze', '--input', str(self.root / 'missing.json')])
        self.assertEqual(status, 2)
        self.assertIn('error', json.loads(output.getvalue()))
        from accessibility import parser
        arguments = parser().parse_args(['analyze', '--input', 'fixture.json', '--direction', 'roundtrip', '--equal-priority'])
        self.assertEqual(arguments.direction, 'roundtrip')
        self.assertTrue(arguments.equal_priority)


if __name__ == '__main__':
    unittest.main()
