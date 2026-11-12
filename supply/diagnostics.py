from collections import defaultdict
from fractions import Fraction
from catchments.pairs import exact, fraction
from supply.pools import connections, capacities
from supply.competition import audit


def unused_explanations(study, catchment, allocation):
    audit(study, catchment, allocation)
    links = connections(study, catchment)
    groups = defaultdict(list)
    for link in links:
        groups[link['pool']].append(link)
    communities = {row['community']: row for row in allocation['communities']}
    output = []
    for row in allocation['pools']:
        unused = exact(row['unused'])
        if unused == 0:
            reason = 'capacity_exhausted'
        elif not groups[row['pool']]:
            reason = 'no_eligible_reachable_community'
        elif all(communities[link['community']]['demand'] == 0 for link in groups[row['pool']]):
            reason = 'reachable_communities_have_zero_demand'
        elif all(exact(communities[link['community']]['unserved']) == 0 for link in groups[row['pool']]):
            reason = 'all_connected_demand_satisfied'
        else:
            raise ValueError('unused capacity adjacent to unsatisfied demand violates maximality')
        output.append({
            'pool': row['pool'],
            'unused': row['unused'],
            'reason': reason,
            'eligible_communities': sorted(link['community'] for link in groups[row['pool']]),
        })
    return output


def competition_components(study, catchment):
    links = connections(study, catchment)
    adjacency = defaultdict(set)
    for ident in study.communities:
        adjacency[('community', ident)]
    for pool in capacities(study):
        adjacency[('pool', pool)]
    for row in links:
        community = 'community', row['community']
        pool = 'pool', row['pool']
        adjacency[community].add(pool)
        adjacency[pool].add(community)
    visited = set()
    output = []
    for origin in sorted(adjacency):
        if origin in visited:
            continue
        stack = [origin]
        visited.add(origin)
        communities = []
        pools = []
        while stack:
            kind, ident = stack.pop()
            (communities if kind == 'community' else pools).append(ident)
            for target in sorted(adjacency[(kind, ident)]):
                if target not in visited:
                    visited.add(target)
                    stack.append(target)
        demand = sum(study.communities[ident]['demand'] for ident in communities)
        capacity = sum(capacities(study)[pool] for pool in pools)
        output.append({
            'communities': sorted(communities),
            'pools': sorted(pools),
            'demand': demand,
            'capacity': capacity,
            'capacity_only_shortfall_lower_bound': max(0, demand - capacity),
            'bound_does_not_account_for_internal_reachability_bottlenecks': True,
        })
    return output


def allocation_travel(allocation):
    total = Fraction(0)
    weighted = Fraction(0)
    by_pool = defaultdict(lambda: [Fraction(0), Fraction(0)])
    for row in allocation['allocations']:
        units = exact(row['amount'])
        total += units
        weighted += units * row['seconds']
        by_pool[row['pool']][0] += units
        by_pool[row['pool']][1] += units * row['seconds']
    rows = []
    for pool, (units, travel) in sorted(by_pool.items()):
        rows.append({'pool': pool, 'served': fraction(units), 'unit_seconds': fraction(travel), 'mean_seconds': fraction(travel / units) if units else None})
    return {'rows': rows, 'served': fraction(total), 'unit_seconds': fraction(weighted), 'mean_seconds': fraction(weighted / total) if total else None, 'interpretation': 'fractional_plan_weighted_travel_not_observed_trip_duration'}


def priority_summary(study, allocation):
    groups = defaultdict(list)
    for row in allocation['communities']:
        groups[study.communities[row['community']]['priority']].append(row)
    rows = []
    for priority, values in sorted(groups.items(), reverse=True):
        demand = sum(row['demand'] for row in values)
        served = sum((exact(row['served']) for row in values), Fraction(0))
        rows.append({'priority': priority, 'demand': demand, 'served': fraction(served), 'served_share': fraction(served / demand) if demand else None})
    return {'priorities_applied': allocation['priorities'], 'rows': rows}
