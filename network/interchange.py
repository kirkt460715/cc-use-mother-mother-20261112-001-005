import csv
import io
import json
from pathlib import Path
from network.contracts import Study, canonical


SCHEMA = {
    'nodes': [('id', str), ('region', str), ('rural', bool)],
    'edges': [('id', str), ('from', str), ('to', str), ('metres', int), ('delay_seconds', int), ('steps', int), ('slope_permille', int), ('closed', bool), ('profiles', list)],
    'profiles': [('id', str), ('seconds_per_km', int), ('step_free', bool), ('max_slope_permille', int), ('max_seconds', int), ('decay', list)],
    'communities': [('id', str), ('node', str), ('profile', str), ('service', str), ('demand', int), ('priority', int), ('max_fee_cents', int)],
    'facilities': [('id', str), ('node', str), ('service', str), ('capacity', int), ('fee_cents', int), ('profiles', list), ('open', bool), ('pool', str)],
}


def parse_cell(value, kind):
    if kind is str:
        return value
    if kind is bool:
        if value not in ('true', 'false'):
            raise ValueError('boolean must be lowercase true or false')
        return value == 'true'
    if kind is int:
        if not value or not value.isascii() or not value.isdigit():
            raise ValueError('integer must be unsigned ASCII digits')
        return int(value)
    if kind is list:
        output = json.loads(value)
        if not isinstance(output, list):
            raise ValueError('list cell requires JSON array')
        return output
    raise ValueError('unsupported CSV type')


def read_table(table, content):
    if table not in SCHEMA or not isinstance(content, str) or len(content.encode()) > 50_000_000:
        raise ValueError('invalid table or content size')
    schema = dict(SCHEMA[table])
    try:
        reader = csv.reader(io.StringIO(content), strict=True)
        header = next(reader, None)
        if header is None or len(header) != len(set(header)) or set(header) != set(schema):
            raise ValueError('table header mismatch')
        rows = []
        for values in reader:
            if len(values) != len(header):
                raise ValueError('table row width mismatch')
            rows.append({key: parse_cell(value, schema[key]) for key, value in zip(header, values)})
            if len(rows) > 100000:
                raise ValueError('table exceeds row limit')
        return rows
    except csv.Error as exc:
        raise ValueError('malformed CSV') from exc


def write_table(study, table):
    if table not in SCHEMA:
        raise ValueError('unknown table')
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, lineterminator='\n')
    writer.writerow([key for key, kind in SCHEMA[table]])
    for row in study.document[table]:
        values = []
        for key, kind in SCHEMA[table]:
            value = row[key]
            if kind is bool:
                value = 'true' if value else 'false'
            elif kind is list:
                value = canonical(value)
            values.append(value)
        writer.writerow(values)
    return stream.getvalue()


def read_directory(path):
    root = Path(path)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('input directory must be regular')
    names = {table + '.csv' for table in SCHEMA}
    if {entry.name for entry in root.iterdir()} != names:
        raise ValueError('input table file set mismatch')
    data = {'version': 1}
    for table in SCHEMA:
        source = root / (table + '.csv')
        if source.is_symlink() or not source.is_file() or source.stat().st_size > 50_000_000:
            raise ValueError('table must be bounded regular file')
        data[table] = read_table(table, source.read_text(encoding='utf-8-sig'))
    return Study(data)


def write_directory(study, path):
    root = Path(path)
    if root.exists() or root.is_symlink():
        raise FileExistsError(root)
    root.mkdir(parents=True)
    paths = []
    try:
        for table in SCHEMA:
            target = root / (table + '.csv')
            target.write_text(write_table(study, table), encoding='utf-8')
            paths.append(target)
    except BaseException:
        for target in paths:
            target.unlink()
        root.rmdir()
        raise
    return {'input_digest': study.fingerprint, 'files': sorted(path.name for path in paths)}


def replace_tables(study, tables):
    if not isinstance(tables, dict) or not tables or tables.keys() - SCHEMA.keys():
        raise ValueError('unknown or empty replacement tables')
    document = study.export()
    for table, content in tables.items():
        document[table] = read_table(table, content)
    return Study(document)
