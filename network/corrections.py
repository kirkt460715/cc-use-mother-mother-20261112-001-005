import copy
from network.contracts import Study, fields, checksum, text
from network.interchange import SCHEMA


def apply(study, operations):
    if not isinstance(operations, list) or len(operations) > 10000:
        raise ValueError('corrections require bounded operation list')
    document = study.export()
    changes = []
    touched = set()
    for operation in operations:
        fields(operation, ('table', 'id', 'action'), ('record',))
        table, ident, action = operation['table'], operation['id'], operation['action']
        text(ident, 'correction.id')
        if table not in SCHEMA or action not in ('add', 'replace', 'remove'):
            raise ValueError('unknown correction table or action')
        if (table, ident) in touched:
            raise ValueError('duplicate correction target')
        touched.add((table, ident))
        matches = [index for index, row in enumerate(document[table]) if row['id'] == ident]
        if action == 'add' and matches:
            raise ValueError('correction add target exists')
        if action != 'add' and len(matches) != 1:
            raise ValueError('correction target must exist uniquely')
        previous = document[table][matches[0]] if matches else None
        if action == 'remove':
            if 'record' in operation:
                raise ValueError('remove cannot contain record')
            del document[table][matches[0]]
            following = None
        else:
            following = copy.deepcopy(operation.get('record'))
            fields(following, [key for key, kind in SCHEMA[table]])
            if following['id'] != ident:
                raise ValueError('correction record identifier mismatch')
            if action == 'add':
                document[table].append(following)
            else:
                document[table][matches[0]] = following
        changes.append({'table': table, 'id': ident, 'action': action, 'before': checksum(previous), 'after': checksum(following)})
    result = Study(document)
    return result, {'before_digest': study.fingerprint, 'after_digest': result.fingerprint, 'changes': changes, 'joint_referential_validation': True}


def differences(before, after):
    operations = []
    for table in SCHEMA:
        old = {row['id']: row for row in before.document[table]}
        new = {row['id']: row for row in after.document[table]}
        for ident in sorted(old.keys() | new.keys()):
            if ident not in old:
                operations.append({'table': table, 'id': ident, 'action': 'add', 'record': new[ident]})
            elif ident not in new:
                operations.append({'table': table, 'id': ident, 'action': 'remove'})
            elif old[ident] != new[ident]:
                operations.append({'table': table, 'id': ident, 'action': 'replace', 'record': new[ident]})
    revised, evidence = apply(before, operations)
    for table in SCHEMA:
        if sorted(revised.document[table], key=lambda row: row['id']) != sorted(after.document[table], key=lambda row: row['id']):
            raise ValueError('correction difference replay mismatch')
    return {'operations': operations, 'evidence': evidence, 'record_order_not_preserved': True}


def reference_impacts(study, table, ident):
    if table not in SCHEMA:
        raise ValueError('unknown table')
    if ident not in {row['id'] for row in study.document[table]}:
        raise KeyError(ident)
    dependents = []
    if table == 'nodes':
        for edge in study.edges.values():
            if ident in (edge['from'], edge['to']):
                dependents.append({'table': 'edges', 'id': edge['id']})
        for name, values in [('communities', study.communities), ('facilities', study.facilities)]:
            for row in values.values():
                if row['node'] == ident:
                    dependents.append({'table': name, 'id': row['id']})
    if table == 'profiles':
        for row in study.communities.values():
            if row['profile'] == ident:
                dependents.append({'table': 'communities', 'id': row['id']})
        for name, values in [('edges', study.edges), ('facilities', study.facilities)]:
            for row in values.values():
                if ident in row['profiles']:
                    dependents.append({'table': name, 'id': row['id']})
    return {'table': table, 'id': ident, 'direct_dependents': sorted(dependents, key=lambda row: (row['table'], row['id'])), 'deletion_requires_joint_corrections': bool(dependents)}
