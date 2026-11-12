from collections import defaultdict
from fractions import Fraction
from network.contracts import checksum
from catchments.pairs import exact, fraction
from supply.pools import capacities, connections


def allocate_tier(demands, pool_remaining, links):
    remaining = {key: Fraction(value) for key, value in demands.items()}
    capacity = {key: Fraction(value) for key, value in pool_remaining.items()}
    allocated = defaultdict(Fraction)
    trace = []
    edges = {(row['community'], row['pool']): exact(row['decay']) for row in links if row['community'] in demands}
    limit = len(demands) + len(capacity) + 1
    for iteration in range(limit):
        offers = {}
        denominators = defaultdict(Fraction)
        for (community, pool), weight in edges.items():
            if remaining[community] > 0 and capacity[pool] > 0:
                denominators[pool] += remaining[community] * weight
        for (community, pool), weight in sorted(edges.items()):
            if remaining[community] > 0 and denominators[pool] > 0 and capacity[pool] > 0:
                offers[(community, pool)] = capacity[pool] * remaining[community] * weight / denominators[pool]
        if not offers:
            break
        by_community = defaultdict(Fraction)
        for (community, pool), offered in offers.items():
            by_community[community] += offered
        transfers = {}
        for key, offered in offers.items():
            community, pool = key
            scale = min(Fraction(1), remaining[community] / by_community[community])
            transfers[key] = offered * scale
        used = defaultdict(Fraction)
        served = defaultdict(Fraction)
        for (community, pool), amount in transfers.items():
            allocated[(community, pool)] += amount
            used[pool] += amount
            served[community] += amount
        for community, value in served.items():
            remaining[community] -= value
            if remaining[community] < 0:
                raise AssertionError('negative remaining demand')
        for pool, value in used.items():
            capacity[pool] -= value
            if capacity[pool] < 0:
                raise AssertionError('negative remaining capacity')
        trace.append({
            'iteration': iteration + 1,
            'transfers': [{'community': key[0], 'pool': key[1], 'amount': fraction(value)} for key, value in sorted(transfers.items()) if value],
            'communities_satisfied': sorted(key for key in served if remaining[key] == 0),
            'pools_exhausted': sorted(key for key in used if capacity[key] == 0),
        })
        if not trace[-1]['communities_satisfied'] and not trace[-1]['pools_exhausted']:
            raise AssertionError('allocation round must eliminate a live constraint')
    else:
        raise AssertionError('progressive allocation exceeded structural round bound')
    return allocated, remaining, capacity, trace


def allocate(study, catchment, priorities=True):
    if type(priorities) is not bool:
        raise ValueError('priorities must be boolean')
    links = connections(study, catchment)
    capacity = {key: Fraction(value) for key, value in capacities(study).items()}
    by_priority = defaultdict(dict)
    for ident, row in study.communities.items():
        priority = row['priority'] if priorities else 0
        by_priority[priority][ident] = row['demand']
    rows = []
    rounds = []
    residual = {}
    selected = {(row['community'], row['pool']): row for row in links}
    for priority, demands in sorted(by_priority.items(), reverse=True):
        transfers, remaining, capacity, trace = allocate_tier(demands, capacity, links)
        residual.update(remaining)
        rounds.append({'priority': priority, 'rounds': trace})
        for (community, pool), amount in sorted(transfers.items()):
            if amount <= 0:
                continue
            link = selected[(community, pool)]
            rows.append({
                'community': community,
                'pool': pool,
                'facility': link['facility'],
                'seconds': link['seconds'],
                'priority': priority,
                'amount': fraction(amount),
            })
    served = defaultdict(Fraction)
    for row in rows:
        served[row['community']] += exact(row['amount'])
    body = {
        'input_digest': study.fingerprint,
        'catchment_digest': catchment['catchment_digest'],
        'priorities': priorities,
        'allocations': rows,
        'communities': [{'community': ident, 'demand': row['demand'], 'served': fraction(served[ident]), 'unserved': fraction(residual[ident])} for ident, row in sorted(study.communities.items())],
        'pools': [{'pool': pool, 'capacity': capacities(study)[pool], 'unused': fraction(value), 'used': fraction(Fraction(capacities(study)[pool]) - value)} for pool, value in sorted(capacity.items())],
        'trace': rounds,
        'interpretation': 'fractional_planning_service_units_not_integer_person_assignments_or_global_optimality',
        'mechanism': 'priority_tiers_then_simultaneous_decay_weighted_offers_with_demand_caps',
    }
    return {**body, 'allocation_digest': checksum(body)}


def audit(study, catchment, report, replay=False):
    if report['input_digest'] != study.fingerprint or report['catchment_digest'] != catchment['catchment_digest']:
        raise ValueError('allocation input mismatch')
    if checksum({key: value for key, value in report.items() if key != 'allocation_digest'}) != report.get('allocation_digest'):
        raise ValueError('allocation checksum mismatch')
    links = {(row['community'], row['pool']): row for row in connections(study, catchment)}
    demand = defaultdict(Fraction)
    capacity = defaultdict(Fraction)
    seen = set()
    for row in report['allocations']:
        key = row['community'], row['pool']
        if key in seen or key not in links or row['facility'] != links[key]['facility']:
            raise ValueError('duplicate or ineligible allocation')
        seen.add(key)
        value = exact(row['amount'])
        if value <= 0:
            raise ValueError('allocation must be positive')
        demand[key[0]] += value
        capacity[key[1]] += value
    if len(report['communities']) != len(study.communities) or {row['community'] for row in report['communities']} != set(study.communities):
        raise ValueError('allocation community set mismatch')
    if len(report['pools']) != len(capacities(study)) or {row['pool'] for row in report['pools']} != set(capacities(study)):
        raise ValueError('allocation pool set mismatch')
    for row in report['communities']:
        ident = row['community']
        if exact(row['served']) != demand[ident] or row['demand'] != study.communities[ident]['demand']:
            raise ValueError('community served total mismatch')
        if exact(row['served']) + exact(row['unserved']) != row['demand'] or exact(row['unserved']) < 0:
            raise ValueError('community demand not conserved')
    for row in report['pools']:
        if exact(row['used']) != capacity[row['pool']] or row['capacity'] != capacities(study)[row['pool']]:
            raise ValueError('pool used total mismatch')
        if exact(row['used']) + exact(row['unused']) != row['capacity'] or exact(row['unused']) < 0:
            raise ValueError('pool capacity not conserved')
    if replay and allocate(study, catchment, report['priorities']) != report:
        raise ValueError('allocation replay mismatch')
    return {'valid': True, 'served': fraction(sum(demand.values(), Fraction(0))), 'used': fraction(sum(capacity.values(), Fraction(0))), 'full_replay': replay}
