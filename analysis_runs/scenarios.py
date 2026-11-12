import copy
from fractions import Fraction
from network.contracts import Study, fields, text, number, checksum
from analysis_runs.pipeline import execute
from equity.comparison import compare
from catchments.pairs import exact, fraction


ALLOWED = {
    'edges': {'closed', 'metres', 'delay_seconds', 'steps', 'slope_permille', 'profiles'},
    'communities': {'demand', 'priority', 'max_fee_cents', 'profile'},
    'facilities': {'open', 'capacity', 'fee_cents', 'profiles'},
    'profiles': {'max_seconds', 'seconds_per_km', 'step_free', 'max_slope_permille', 'decay'},
}


def modify(study, patches):
    if not isinstance(patches, list) or len(patches) > 10000:
        raise ValueError('scenario patches must be bounded list')
    data = study.export()
    seen = set()
    for patch in patches:
        fields(patch, ('table', 'id', 'field', 'value'))
        table, ident, field = patch['table'], patch['id'], patch['field']
        if table not in ALLOWED or field not in ALLOWED[table]:
            raise ValueError('unsupported scenario target')
        key = table, ident, field
        if key in seen:
            raise ValueError('duplicate scenario target')
        seen.add(key)
        matches = [row for row in data[table] if row['id'] == ident]
        if len(matches) != 1:
            raise ValueError('scenario target must exist uniquely')
        matches[0][field] = copy.deepcopy(patch['value'])
    return Study(data)


def pool_capacity_patches(study, pool, capacity):
    number(capacity, 'capacity', 0, 10000000)
    members = [row for row in study.facilities.values() if row['pool'] == pool]
    if not members:
        raise KeyError(pool)
    return [{'table': 'facilities', 'id': row['id'], 'field': 'capacity', 'value': capacity} for row in sorted(members, key=lambda row: row['id'])]


def evaluate_cases(study, cases, direction='outbound', priorities=True):
    if not isinstance(cases, list) or len(cases) > 50:
        raise ValueError('cases must be bounded list')
    names = set()
    baseline = execute(study, direction, priorities)
    output = []
    for case in cases:
        fields(case, ('name', 'patches'))
        text(case['name'], 'case.name')
        if case['name'] in names:
            raise ValueError('duplicate case name')
        names.add(case['name'])
        variant = modify(study, case['patches'])
        result = execute(variant, direction, priorities)
        output.append({'name': case['name'], 'input_digest': variant.fingerprint, 'result_digest': result['result_digest'], 'comparison': compare(baseline, result), 'overall': result['overall']})
    return {'baseline_digest': baseline['result_digest'], 'cases': output}


def capacity_sweep(study, pool, values, direction='outbound', priorities=True):
    if not isinstance(values, list) or len(values) > 50 or len(values) != len(set(values)):
        raise ValueError('capacity sweep requires bounded unique values')
    output = []
    previous = None
    for capacity in sorted(values):
        variant = modify(study, pool_capacity_patches(study, pool, capacity))
        result = execute(variant, direction, priorities)
        served = sum((exact(row['served']) for row in result['allocation']['communities']), Fraction(0))
        marginal = None
        if previous is not None:
            added = capacity - previous[0]
            marginal = (served - previous[1]) / added
        output.append({'capacity': capacity, 'served': fraction(served), 'marginal_served_per_added_unit': fraction(marginal) if marginal is not None else None, 'result_digest': result['result_digest']})
        previous = capacity, served
    return {'pool': pool, 'rows': output, 'not_an_optimal_site_selection': True}


def outage(study, edge_ids=(), facility_ids=(), direction='outbound', priorities=True):
    if len(edge_ids) != len(set(edge_ids)) or len(facility_ids) != len(set(facility_ids)):
        raise ValueError('outage ids must be unique')
    patches = [{'table': 'edges', 'id': ident, 'field': 'closed', 'value': True} for ident in edge_ids]
    patches.extend({'table': 'facilities', 'id': ident, 'field': 'open', 'value': False} for ident in facility_ids)
    variant = modify(study, patches)
    before = execute(study, direction, priorities)
    after = execute(variant, direction, priorities)
    return {'closed_edges': sorted(edge_ids), 'closed_facilities': sorted(facility_ids), 'comparison': compare(before, after), 'after': after['overall']}


def dependency_scope(patches):
    scopes = set()
    for patch in patches:
        table, field = patch['table'], patch['field']
        if table == 'edges' or table == 'profiles' or table == 'communities' and field == 'profile':
            scopes.update(['routing', 'catchments', 'supply', 'equity'])
        elif table == 'communities' and field in ('demand', 'priority'):
            scopes.update(['supply', 'equity'])
        elif table == 'facilities' and field == 'capacity':
            scopes.update(['supply', 'equity'])
        else:
            scopes.update(['catchments', 'supply', 'equity'])
    return [stage for stage in ('routing', 'catchments', 'supply', 'equity') if stage in scopes]
