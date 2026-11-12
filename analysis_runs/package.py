import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from network.contracts import Study, canonical
from analysis_runs.pipeline import audit
from network.topology import profile_topology
from catchments.diagnostics import restriction_counterfactual, travel_frontier, route_edge_exposure
from equity.disclosure import public_packet, verify as verify_public


def contents(study, result, minimum_communities=3, minimum_demand=10):
    checked = audit(study, result, replay=True)
    public = public_packet(result['community_metrics'], result['result_digest'], minimum_communities, minimum_demand)
    verify_public(public)
    documents = {
        'study.json': study.document,
        'result.json': result,
        'audit.json': checked,
        'public.json': public,
        'topology.json': profile_topology(study),
        'barriers.json': restriction_counterfactual(study),
        'frontiers.json': travel_frontier(study, result['catchment']),
        'route-exposure.json': route_edge_exposure(study, result['catchment']),
    }
    files = {name: canonical(document) for name, document in documents.items()}
    manifest = {
        'version': 1,
        'input_digest': study.fingerprint,
        'result_digest': result['result_digest'],
        'minimum_communities': minimum_communities,
        'minimum_demand': minimum_demand,
        'access': 'restricted_contains_local_synthetic_or_authorized_community_routes',
        'files': {name: hashlib.sha256(content.encode()).hexdigest() for name, content in sorted(files.items())},
    }
    files['manifest.json'] = canonical(manifest)
    return files


def write(study, result, path, minimum_communities=3, minimum_demand=10):
    files = contents(study, result, minimum_communities, minimum_demand)
    target = Path(path)
    if target.exists() or target.is_symlink():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.access-', dir=target.parent))
    try:
        for name, content in files.items():
            with (staging / name).open('w', encoding='utf-8') as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
        os.rename(staging, target)
    except BaseException:
        shutil.rmtree(staging)
        raise
    return verify(target)


def verify(path):
    root = Path(path)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('package must be regular directory')
    manifest_path = root / 'manifest.json'
    if manifest_path.is_symlink() or not manifest_path.is_file() or manifest_path.stat().st_size > 100000:
        raise ValueError('invalid manifest')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('version') != 1 or not isinstance(manifest.get('files'), dict):
        raise ValueError('invalid package manifest version')
    if set(manifest['files']) | {'manifest.json'} != {entry.name for entry in root.iterdir()}:
        raise ValueError('package file set mismatch')
    for name, expected in manifest['files'].items():
        if Path(name).name != name or name in ('.', '..'):
            raise ValueError('unsafe manifest path')
        source = root / name
        if source.is_symlink() or not source.is_file() or source.stat().st_size > 100_000_000:
            raise ValueError('package files must be bounded and regular')
        if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise ValueError('package checksum mismatch')
    study = Study(json.loads((root / 'study.json').read_text(encoding='utf-8')))
    result = json.loads((root / 'result.json').read_text(encoding='utf-8'))
    files = contents(study, result, manifest['minimum_communities'], manifest['minimum_demand'])
    for name, expected in files.items():
        if (root / name).read_bytes() != expected.encode():
            raise ValueError('derived package replay mismatch')
    return {'valid': True, 'files': len(files), 'input_digest': study.fingerprint, 'result_digest': result['result_digest']}
