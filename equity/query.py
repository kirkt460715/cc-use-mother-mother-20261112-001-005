import base64
import json
from fractions import Fraction
from network.contracts import checksum, canonical, number
from catchments.pairs import exact, fraction
from equity.metrics import aggregate


FILTERS = {'region', 'rural', 'profile', 'service', 'priority', 'community'}
COLUMNS = {'community', 'region', 'rural', 'profile', 'service', 'priority', 'demand', 'geographically_covered', 'nearest_seconds', 'eligible_pools', 'accessibility', 'served', 'unserved', 'served_share', 'no_capacity_after_competition', 'excluded_facilities'}


def token(value):
    return base64.urlsafe_b64encode(canonical(value).encode()).decode().rstrip('=')


def untoken(value):
    if not isinstance(value, str) or len(value) > 10000:
        raise ValueError('invalid cursor text')
    try:
        parsed = json.loads(base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True))
    except (ValueError, UnicodeError) as exc:
        raise ValueError('invalid cursor content') from exc
    if not isinstance(parsed, dict):
        raise ValueError('cursor requires object')
    return parsed


def select(result, filters=None, columns=None, order='community', descending=False, limit=100, cursor=None):
    filters = {} if filters is None else filters
    columns = sorted(COLUMNS) if columns is None else columns
    if not isinstance(filters, dict) or filters.keys() - FILTERS:
        raise ValueError('unsupported community filter')
    for key, values in filters.items():
        if not isinstance(values, list) or len(values) > 1000:
            raise ValueError('filter values must be bounded list')
        if any(type(value) not in (str, int, bool) for value in values):
            raise ValueError('filter values must be scalar')
        if len(values) != len(set((type(value).__name__, value) for value in values)):
            raise ValueError('duplicate filter value')
    if not isinstance(columns, list) or not columns or len(columns) != len(set(columns)) or set(columns) - COLUMNS:
        raise ValueError('invalid query columns')
    if order not in ('community', 'demand', 'nearest_seconds', 'served', 'unserved', 'accessibility'):
        raise ValueError('unsupported ordering')
    if type(descending) is not bool:
        raise ValueError('descending must be boolean')
    number(limit, 'limit', 1, 1000)
    context = checksum({'result': result['result_digest'], 'filters': filters, 'columns': columns, 'order': order, 'descending': descending})
    selected = []
    for row in result['community_metrics']:
        if all(any(type(row[key]) is type(value) and row[key] == value for value in values) for key, values in filters.items()):
            selected.append(row)
    def sort_value(row):
        value = row[order]
        if isinstance(value, dict):
            value = exact(value)
        if value is None:
            return (1, 0, row['community'])
        if descending and isinstance(value, (int, Fraction)):
            value = -value
        return (0, value, row['community'])
    if order == 'community' and descending:
        selected.sort(key=lambda row: row['community'], reverse=True)
    else:
        selected.sort(key=sort_value)
    offset = 0
    if cursor is not None:
        parsed = untoken(cursor)
        if set(parsed) != {'context', 'offset'} or parsed['context'] != context:
            raise ValueError('cursor belongs to another result or query')
        offset = number(parsed['offset'], 'offset', 0, len(selected))
    page = selected[offset:offset + limit]
    next_offset = offset + len(page)
    return {
        'result_digest': result['result_digest'],
        'total': len(selected),
        'rows': [{key: row[key] for key in columns} for row in page],
        'next_cursor': token({'context': context, 'offset': next_offset}) if next_offset < len(selected) else None,
        'access': 'restricted_community_metrics',
    }


def rank_shortfalls(result, limit=20):
    number(limit, 'limit', 1, 1000)
    rows = sorted(result['community_metrics'], key=lambda row: (-exact(row['unserved']), -row['priority'], row['community']))
    total = sum((exact(row['unserved']) for row in rows), Fraction(0))
    cumulative = Fraction(0)
    output = []
    for index, row in enumerate(rows[:limit], 1):
        cumulative += exact(row['unserved'])
        output.append({
            'rank': index,
            'community': row['community'],
            'priority': row['priority'],
            'unserved': row['unserved'],
            'cumulative_shortfall_share': fraction(cumulative / total) if total else None,
        })
    return {'rows': output, 'total_shortfall': fraction(total), 'ranking': 'unserved_units_then_priority_then_id'}
