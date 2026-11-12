from contextlib import contextmanager
import json
import sqlite3
from network.contracts import Study, checksum, canonical, text, number
from network.routing import Graph


class RouteCache:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path), isolation_level=None, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS graph_inputs(
            digest TEXT PRIMARY KEY,
            document TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS route_trees(
            key TEXT PRIMARY KEY,
            input_digest TEXT NOT NULL REFERENCES graph_inputs(digest),
            profile TEXT NOT NULL,
            source TEXT NOT NULL,
            reversed INTEGER NOT NULL,
            maximum INTEGER,
            body TEXT NOT NULL,
            body_digest TEXT NOT NULL,
            hits INTEGER NOT NULL DEFAULT 0
        );
        ''')

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
        except BaseException:
            self.db.execute('ROLLBACK')
            raise
        else:
            self.db.execute('COMMIT')

    def register(self, study):
        with self.transaction():
            current = self.db.execute('SELECT document FROM graph_inputs WHERE digest=?', (study.fingerprint,)).fetchone()
            if current is None:
                self.db.execute('INSERT INTO graph_inputs VALUES(?,?)', (study.fingerprint, canonical(study.document)))
            elif current[0] != canonical(study.document):
                raise ValueError('source digest collision or corruption')
        return study.fingerprint

    def source(self, fingerprint):
        row = self.db.execute('SELECT document FROM graph_inputs WHERE digest=?', (fingerprint,)).fetchone()
        if row is None:
            raise KeyError(fingerprint)
        study = Study(json.loads(row[0]))
        if study.fingerprint != fingerprint:
            raise ValueError('cached source checksum mismatch')
        return study

    def key(self, study, profile, source, reverse, maximum):
        if type(reverse) is not bool:
            raise ValueError('reverse must be boolean')
        if profile not in study.profiles or source not in study.nodes:
            raise ValueError('unknown route source or profile')
        if maximum is not None:
            number(maximum, 'maximum', 0, 10**12)
        return checksum({'input': study.fingerprint, 'profile': profile, 'source': source, 'reverse': reverse, 'maximum': maximum, 'cost_version': 1})

    def get(self, study, profile, source, reverse=False, maximum=None, cached_only=False):
        key = self.key(study, profile, source, reverse, maximum)
        current = self.db.execute('SELECT * FROM route_trees WHERE key=?', (key,)).fetchone()
        if current is not None:
            body = json.loads(current['body'])
            if checksum(body) != current['body_digest']:
                raise ValueError('cached route tree checksum mismatch')
            graph = Graph(study, profile, reverse=reverse)
            if body['graph_digest'] != graph.fingerprint or body['source'] != source:
                raise ValueError('cached route tree context mismatch')
            self.db.execute('UPDATE route_trees SET hits=hits+1 WHERE key=?', (key,))
            return {'cached': True, 'key': key, 'tree': body}
        if cached_only:
            raise KeyError('route tree absent from local cache')
        self.register(study)
        graph = Graph(study, profile, reverse=reverse)
        tree = graph.shortest(source, maximum)
        with self.transaction():
            existing = self.db.execute('SELECT body_digest FROM route_trees WHERE key=?', (key,)).fetchone()
            if existing and existing[0] != checksum(tree):
                raise ValueError('deterministic route cache collision')
            self.db.execute('INSERT OR IGNORE INTO route_trees(key,input_digest,profile,source,reversed,maximum,body,body_digest) VALUES(?,?,?,?,?,?,?,?)', (key, study.fingerprint, profile, source, int(reverse), maximum, canonical(tree), checksum(tree)))
        return {'cached': False, 'key': key, 'tree': tree}

    def prewarm(self, study):
        keys = sorted({(community['profile'], community['node']) for community in study.communities.values()})
        results = []
        for profile, source in keys:
            for reverse in (False, True):
                cached = self.get(study, profile, source, reverse)
                results.append({'profile': profile, 'source': source, 'reverse': reverse, 'key': cached['key'], 'cached': cached['cached']})
        return {'input_digest': study.fingerprint, 'routes': results}

    def audit(self):
        if self.db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('route cache SQLite integrity failure')
        count = 0
        for row in self.db.execute('SELECT * FROM route_trees ORDER BY key'):
            study = self.source(row['input_digest'])
            reverse = bool(row['reversed'])
            expected_key = self.key(study, row['profile'], row['source'], reverse, row['maximum'])
            body = json.loads(row['body'])
            if expected_key != row['key'] or checksum(body) != row['body_digest']:
                raise ValueError('route cache row integrity failure')
            Graph(study, row['profile'], reverse).audit_tree(body, row['maximum'])
            count += 1
        return {'valid': True, 'route_trees': count}

    def evict(self, fingerprint):
        with self.transaction():
            removed = self.db.execute('DELETE FROM route_trees WHERE input_digest=?', (fingerprint,)).rowcount
            self.db.execute('DELETE FROM graph_inputs WHERE digest=?', (fingerprint,))
        return {'removed_trees': removed, 'input_digest': fingerprint}

    def statistics(self):
        rows = self.db.execute('SELECT input_digest,COUNT(*) AS trees,SUM(hits) AS hits FROM route_trees GROUP BY input_digest ORDER BY input_digest').fetchall()
        return {'rows': [dict(row) for row in rows], 'cache_scope': 'exact_input_cost_profile_source_orientation_and_threshold'}
