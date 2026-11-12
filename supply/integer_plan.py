from collections import defaultdict
from fractions import Fraction
from catchments.pairs import exact, fraction
from supply.competition import audit
from supply.pools import capacities, connections


def conservative_plan(study, catchment, allocation):
    audit(study, catchment, allocation)
    pool_left = capacities(study)
    demand_left = {ident: row['demand'] for ident, row in study.communities.items()}
    planned = defaultdict(int)
    remainders = []
    for row in allocation['allocations']:
        amount = exact(row['amount'])
        floor = amount.numerator // amount.denominator
        key = row['community'], row['pool'], row['facility']
        planned[key] = floor
        demand_left[key[0]] -= floor
        pool_left[key[1]] -= floor
        remainders.append((amount - floor, row))
    order = sorted(remainders, key=lambda item: (-(study.communities[item[1]['community']]['priority'] if allocation['priorities'] else 0), -item[0], item[1]['seconds'], item[1]['community'], item[1]['pool']))
    for remainder, row in order:
        if not remainder:
            continue
        community, pool = row['community'], row['pool']
        if demand_left[community] > 0 and pool_left[pool] > 0:
            planned[(community, pool, row['facility'])] += 1
            demand_left[community] -= 1
            pool_left[pool] -= 1
    report = {
        'allocation_digest': allocation['allocation_digest'],
        'rows': [{'community': key[0], 'pool': key[1], 'facility': key[2], 'units': value} for key, value in sorted(planned.items()) if value],
        'unserved': [{'community': ident, 'units': value} for ident, value in sorted(demand_left.items())],
        'unused': [{'pool': ident, 'units': value} for ident, value in sorted(pool_left.items())],
        'scope': 'feasible_floor_plus_one_pass_largest_remainders_not_maximum_throughput_or_fair_integer_optimum',
        'priority_rounding': allocation['priorities'],
    }
    audit_plan(study, catchment, report)
    return report


def audit_plan(study, catchment, report):
    links = {(row['community'], row['pool']): row for row in connections(study, catchment)}
    by_community = defaultdict(int)
    by_pool = defaultdict(int)
    seen = set()
    for row in report['rows']:
        key = row['community'], row['pool']
        if key not in links or key in seen or links[key]['facility'] != row['facility']:
            raise ValueError('invalid integer plan connection')
        if type(row['units']) is not int or row['units'] <= 0:
            raise ValueError('integer plan must have positive integer units')
        seen.add(key)
        by_community[key[0]] += row['units']
        by_pool[key[1]] += row['units']
    residual = {row['community']: row['units'] for row in report['unserved']}
    unused = {row['pool']: row['units'] for row in report['unused']}
    if set(residual) != set(study.communities) or set(unused) != set(capacities(study)):
        raise ValueError('integer plan residual set mismatch')
    for ident, community in study.communities.items():
        if type(residual[ident]) is not int or residual[ident] < 0 or by_community[ident] + residual[ident] != community['demand']:
            raise ValueError('integer community total mismatch')
    for ident, capacity in capacities(study).items():
        if type(unused[ident]) is not int or unused[ident] < 0 or by_pool[ident] + unused[ident] != capacity:
            raise ValueError('integer pool total mismatch')
    return {'valid': True, 'served': sum(by_community.values()), 'unserved': sum(residual.values())}
