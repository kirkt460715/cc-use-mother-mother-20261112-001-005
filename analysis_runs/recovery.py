import hashlib
import json
from pathlib import Path
import sqlite3
from network.contracts import checksum, Study
from analysis_runs.workspace import Workspace
from analysis_runs.pipeline import audit as audit_pipeline
from analysis_runs.scenarios import modify


def audit(workspace):
    if workspace.db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise ValueError('workspace integrity failure')
    if workspace.db.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('workspace foreign key failure')
    sources = [row[0] for row in workspace.db.execute('SELECT digest FROM studies')]
    for fingerprint in sources:
        workspace.study(fingerprint)
    branches = [row[0] for row in workspace.db.execute('SELECT name FROM branches')]
    for name in branches:
        current = workspace.branch(name)
        events = workspace.db.execute('SELECT * FROM branch_history WHERE name=? ORDER BY revision', (name,)).fetchall()
        previous = None
        previous_study = None
        for revision, row in enumerate(events, 1):
            body = json.loads(row['body'])
            if row['revision'] != revision or body['revision'] != revision or body['name'] != name:
                raise ValueError('branch history sequence mismatch')
            if body['previous'] != previous or checksum(body) != row['digest']:
                raise ValueError('branch history hash mismatch')
            study = workspace.study(body['study'])
            parent = body['parent']
            if parent is not None:
                ancestor = workspace.study(parent['study'])
                revised = modify(ancestor, body['patches'])
                if revised.fingerprint != study.fingerprint:
                    raise ValueError('branch patch replay mismatch')
                if revision > 1 and parent['study'] != previous_study:
                    raise ValueError('branch parent study mismatch')
            previous = row['digest']
            previous_study = study.fingerprint
        if len(events) != current['revision'] or previous_study != current['study']:
            raise ValueError('branch head/history mismatch')
    result_keys = [row[0] for row in workspace.db.execute('SELECT key FROM results')]
    for key in result_keys:
        result = workspace.result(key)
        audit_pipeline(workspace.study(result['input_digest']), result, replay=True)
    labels = [row[0] for row in workspace.db.execute('SELECT label FROM publications')]
    for label in labels:
        workspace.publication(label)
    return {'valid': True, 'studies': len(sources), 'branches': len(branches), 'results': len(result_keys), 'publications': len(labels)}


def backup(workspace, path):
    audit(workspace)
    target = Path(path)
    if target.exists() or target.is_symlink():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch(exist_ok=False)
    try:
        connection = sqlite3.connect(str(target))
        try:
            workspace.db.backup(connection)
        finally:
            connection.close()
        with Workspace(target) as restored:
            evidence = audit(restored)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return {'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'audit': evidence}


def restore(source, destination, sha256):
    origin, target = Path(source), Path(destination)
    if origin.is_symlink() or not origin.is_file():
        raise ValueError('backup must be regular file')
    if hashlib.sha256(origin.read_bytes()).hexdigest() != sha256:
        raise ValueError('backup checksum mismatch')
    if target.exists() or target.is_symlink():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch(exist_ok=False)
    try:
        connection = sqlite3.connect(origin.resolve().as_uri() + '?mode=ro', uri=True)
        output = sqlite3.connect(str(target))
        try:
            connection.backup(output)
        finally:
            output.close()
            connection.close()
        with Workspace(target) as restored:
            evidence = audit(restored)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return {'restored': True, 'audit': evidence}
