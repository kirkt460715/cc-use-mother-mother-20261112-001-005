from network.contracts import checksum
from catchments.pairs import build, verify as verify_catchment
from supply.pools import floating_catchment, audit_index
from supply.competition import allocate, audit as audit_allocation
from supply.integer_plan import conservative_plan, audit_plan
from equity.metrics import observations, aggregate, rural_urban_gap, threshold_coverage
from catchments.diagnostics import community_diagnostics


def execute(study, direction='outbound', priorities=True):
    catchment = build(study, direction)
    index_report = floating_catchment(study, catchment)
    allocation = allocate(study, catchment, priorities)
    integer = conservative_plan(study, catchment, allocation)
    rows = observations(study, catchment, index_report, allocation)
    body = {
        'version': 1,
        'input_digest': study.fingerprint,
        'direction': direction,
        'priorities': priorities,
        'catchment': catchment,
        'floating_index': index_report,
        'allocation': allocation,
        'integer_plan': integer,
        'community_metrics': rows,
        'diagnostics': community_diagnostics(study, catchment),
        'overall': aggregate(rows, []),
        'by_region': aggregate(rows, ['region']),
        'by_service_profile': aggregate(rows, ['service', 'profile']),
        'rural_urban': rural_urban_gap(rows),
        'limitations': ['static_graph_costs', 'fractional_planning_not_actual_person_assignment', 'no_global_assignment_optimality_claim', 'floating_index_is_not_coverage_probability'],
    }
    return {**body, 'result_digest': checksum(body)}


def audit(study, result, replay=False):
    if result['input_digest'] != study.fingerprint:
        raise ValueError('pipeline source mismatch')
    body = {key: value for key, value in result.items() if key != 'result_digest'}
    if checksum(body) != result.get('result_digest'):
        raise ValueError('pipeline result checksum mismatch')
    if result['direction'] != result['catchment']['direction']:
        raise ValueError('pipeline journey orientation mismatch')
    if type(result['priorities']) is not bool or result['priorities'] != result['allocation']['priorities']:
        raise ValueError('pipeline priority policy mismatch')
    verify_catchment(study, result['catchment'], replay=replay)
    index = audit_index(study, result['catchment'], result['floating_index'])
    allocation = audit_allocation(study, result['catchment'], result['allocation'], replay=replay)
    integer = audit_plan(study, result['catchment'], result['integer_plan'])
    rows = observations(study, result['catchment'], result['floating_index'], result['allocation'])
    if rows != result['community_metrics']:
        raise ValueError('community metrics reconstruction mismatch')
    if aggregate(rows, []) != result['overall']:
        raise ValueError('overall aggregation mismatch')
    if replay and execute(study, result['direction'], result['priorities']) != result:
        raise ValueError('pipeline full replay mismatch')
    return {'valid': True, 'index': index, 'allocation': allocation, 'integer': integer, 'full_replay': replay}
