from contextlib import contextmanager
import json
import sqlite3
from network.contracts import Study, canonical, checksum, text, number
from analysis_runs.pipeline import execute, audit
from analysis_runs.scenarios import modify
from equity.disclosure import public_packet, verify as verify_public


class RevisionConflict(ValueError):
    pass


class Workspace:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path), timeout=5, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS studies(
            digest TEXT PRIMARY KEY,
            body TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS branches(
            name TEXT PRIMARY KEY,
            study TEXT NOT NULL REFERENCES studies(digest),
            revision INTEGER NOT NULL,
            parent TEXT,
            patch TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS results(
            key TEXT PRIMARY KEY,
            study TEXT NOT NULL REFERENCES studies(digest),
            direction TEXT NOT NULL,
            priorities INTEGER NOT NULL,
            body TEXT NOT NULL,
            digest TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS publications(
            label TEXT PRIMARY KEY,
            result_key TEXT NOT NULL REFERENCES results(key),
            body TEXT NOT NULL,
            digest TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS branch_history(
            name TEXT NOT NULL,
            revision INTEGER NOT NULL,
            body TEXT NOT NULL,
            digest TEXT NOT NULL,
            PRIMARY KEY(name,revision)
        );
        ''')

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @contextmanager
    def atomic(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
        except BaseException:
            self.db.execute('ROLLBACK')
            raise
        else:
            self.db.execute('COMMIT')

    def _source(self, study):
        row = self.db.execute('SELECT body FROM studies WHERE digest=?', (study.fingerprint,)).fetchone()
        if row and row[0] != canonical(study.document):
            raise ValueError('study digest collision')
        self.db.execute('INSERT OR IGNORE INTO studies VALUES(?,?)', (study.fingerprint, canonical(study.document)))

    def study(self, fingerprint):
        row = self.db.execute('SELECT body FROM studies WHERE digest=?', (fingerprint,)).fetchone()
        if row is None:
            raise KeyError(fingerprint)
        study = Study(json.loads(row[0]))
        if study.fingerprint != fingerprint:
            raise ValueError('stored study checksum mismatch')
        return study

    def create(self, name, study):
        text(name, 'branch.name')
        with self.atomic():
            current = self.db.execute('SELECT study FROM branches WHERE name=?', (name,)).fetchone()
            if current:
                if current[0] != study.fingerprint:
                    raise RevisionConflict('branch already exists with another study')
            else:
                self._source(study)
                self.db.execute('INSERT INTO branches VALUES(?,?,1,NULL,?)', (name, study.fingerprint, '[]'))
                self._history(name, 1, study.fingerprint, None, [])
        return self.branch(name)

    def branch(self, name):
        row = self.db.execute('SELECT * FROM branches WHERE name=?', (name,)).fetchone()
        if row is None:
            raise KeyError(name)
        value = dict(row)
        value['patch'] = json.loads(value['patch'])
        self.study(value['study'])
        return value

    def _history(self, name, revision, fingerprint, parent, patches):
        previous = self.db.execute('SELECT digest FROM branch_history WHERE name=? ORDER BY revision DESC LIMIT 1', (name,)).fetchone()
        body = {'name': name, 'revision': revision, 'study': fingerprint, 'parent': parent, 'patches': patches, 'previous': previous[0] if previous else None}
        self.db.execute('INSERT INTO branch_history VALUES(?,?,?,?)', (name, revision, canonical(body), checksum(body)))

    def fork(self, source, target, expected_revision, patches):
        number(expected_revision, 'revision', 1)
        text(target, 'branch.target')
        with self.atomic():
            current = self.branch(source)
            if current['revision'] != expected_revision:
                raise RevisionConflict('source branch changed')
            if self.db.execute('SELECT name FROM branches WHERE name=?', (target,)).fetchone():
                raise RevisionConflict('fork target exists')
            study = modify(self.study(current['study']), patches)
            self._source(study)
            parent = {'branch': source, 'revision': expected_revision, 'study': current['study']}
            self.db.execute('INSERT INTO branches VALUES(?,?,1,?,?)', (target, study.fingerprint, canonical(parent), canonical(patches)))
            self._history(target, 1, study.fingerprint, parent, patches)
        return self.branch(target)

    def revise(self, name, expected_revision, patches):
        number(expected_revision, 'revision', 1)
        with self.atomic():
            current = self.branch(name)
            if current['revision'] != expected_revision:
                raise RevisionConflict('branch revision is stale')
            study = modify(self.study(current['study']), patches)
            self._source(study)
            self.db.execute('UPDATE branches SET study=?,revision=revision+1,patch=? WHERE name=?', (study.fingerprint, canonical(patches), name))
            parent = {'branch': name, 'revision': expected_revision, 'study': current['study']}
            self._history(name, expected_revision + 1, study.fingerprint, parent, patches)
        return self.branch(name)

    def analyze(self, name, expected_revision, direction='outbound', priorities=True):
        number(expected_revision, 'revision', 1)
        current = self.branch(name)
        if current['revision'] != expected_revision:
            raise RevisionConflict('branch revision is stale')
        study = self.study(current['study'])
        key = checksum({'study': study.fingerprint, 'direction': direction, 'priorities': priorities, 'algorithm_version': 1})
        existing = self.db.execute('SELECT key FROM results WHERE key=?', (key,)).fetchone()
        if existing:
            return {'key': key, 'reused': True, 'result': self.result(key)}
        result = execute(study, direction, priorities)
        audit(study, result)
        with self.atomic():
            latest = self.branch(name)
            if latest['revision'] != expected_revision:
                raise RevisionConflict('branch changed during analysis; no result committed')
            row = self.db.execute('SELECT digest FROM results WHERE key=?', (key,)).fetchone()
            if row and row[0] != result['result_digest']:
                raise ValueError('deterministic result collision')
            self.db.execute('INSERT OR IGNORE INTO results VALUES(?,?,?,?,?,?)', (key, study.fingerprint, direction, int(priorities), canonical(result), result['result_digest']))
        return {'key': key, 'reused': False, 'result': result}

    def result(self, key):
        row = self.db.execute('SELECT * FROM results WHERE key=?', (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        result = json.loads(row['body'])
        if result['result_digest'] != row['digest']:
            raise ValueError('stored result digest mismatch')
        audit(self.study(row['study']), result)
        expected = checksum({'study': row['study'], 'direction': row['direction'], 'priorities': bool(row['priorities']), 'algorithm_version': 1})
        if expected != key or result['direction'] != row['direction'] or result['priorities'] != bool(row['priorities']):
            raise ValueError('stored result key mismatch')
        return result

    def publish(self, label, key, minimum_communities=3, minimum_demand=10):
        text(label, 'publication.label')
        result = self.result(key)
        packet = public_packet(result['community_metrics'], result['result_digest'], minimum_communities, minimum_demand)
        verify_public(packet)
        with self.atomic():
            existing = self.db.execute('SELECT * FROM publications WHERE label=?', (label,)).fetchone()
            if existing and (existing['result_key'] != key or existing['digest'] != packet['public_digest']):
                raise RevisionConflict('publication label is immutable')
            self.db.execute('INSERT OR IGNORE INTO publications VALUES(?,?,?,?)', (label, key, canonical(packet), packet['public_digest']))
        return self.publication(label)

    def publication(self, label):
        row = self.db.execute('SELECT * FROM publications WHERE label=?', (label,)).fetchone()
        if row is None:
            raise KeyError(label)
        body = json.loads(row['body'])
        verify_public(body)
        if row['digest'] != body['public_digest'] or self.result(row['result_key'])['result_digest'] != body['result_digest']:
            raise ValueError('publication binding mismatch')
        return {'label': label, 'result_key': row['result_key'], 'packet': body}
