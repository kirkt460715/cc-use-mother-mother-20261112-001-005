from collections import defaultdict
from fractions import Fraction
from catchments.pairs import exact, fraction
from supply.pools import capacities, connections
from supply.competition import audit


def pool_service_mix(study, catchment, allocation):
    audit(study, catchment, allocation)
    groups = defaultdict(lambda: defaultdict(Fraction))
    for row in allocation['allocations']:
        service = study.communities[row['community']]['service']
        groups[row['pool']][service] += exact(row['amount'])
    output = []
    for pool, capacity in sorted(capacities(study).items()):
        services = []
        total = sum(groups[pool].values(), Fraction(0))
        for service, units in sorted(groups[pool].items()):
            services.append({'service': service, 'units': fraction(units), 'share_of_used_pool': fraction(units / total) if total else None})
        output.append({
            'pool': pool,
            'capacity': capacity,
            'used': fraction(total),
            'unused': fraction(Fraction(capacity) - total),
            'service_mix': services,
            'capacity_shared_between_service_types': len({row['service'] for row in study.facilities.values() if row['pool'] == pool}) > 1,
        })
    return output


def community_competitors(study, catchment, community_id):
    if community_id not in study.communities:
        raise KeyError(community_id)
    links = connections(study, catchment)
    own = {row['pool']: row for row in links if row['community'] == community_id}
    neighbors = defaultdict(list)
    for row in links:
        if row['pool'] in own and row['community'] != community_id:
            neighbors[row['community']].append(row['pool'])
    rows = []
    for ident, pools in sorted(neighbors.items()):
        community = study.communities[ident]
        rows.append({
            'community': ident,
            'shared_pools': sorted(pools),
            'demand': community['demand'],
            'service': community['service'],
            'priority': community['priority'],
            'higher_priority': community['priority'] > study.communities[community_id]['priority'],
            'cross_service_competition': community['service'] != study.communities[community_id]['service'],
        })
    return {'community': community_id, 'pools': sorted(own), 'competitors': rows, 'scope': 'eligibility_network_competitors_not_proof_of_displacement'}


def priority_displacement(study, catchment):
    from supply.competition import allocate
    prioritized = allocate(study, catchment, True)
    equal = allocate(study, catchment, False)
    before = {row['community']: exact(row['served']) for row in equal['communities']}
    after = {row['community']: exact(row['served']) for row in prioritized['communities']}
    rows = []
    for ident, community in sorted(study.communities.items()):
        rows.append({
            'community': ident,
            'priority': community['priority'],
            'equal_priority_served': fraction(before[ident]),
            'priority_served': fraction(after[ident]),
            'difference': fraction(after[ident] - before[ident]),
        })
    return {
        'rows': rows,
        'equal_total_served': fraction(sum(before.values(), Fraction(0))),
        'priority_total_served': fraction(sum(after.values(), Fraction(0))),
        'total_throughput_may_change_with_priority_policy': True,
    }


def single_pool_dependency(study, catchment, allocation):
    audit(study, catchment, allocation)
    groups = defaultdict(list)
    for row in connections(study, catchment):
        groups[row['community']].append(row)
    served = {row['community']: exact(row['served']) for row in allocation['communities']}
    rows = []
    for ident, options in sorted(groups.items()):
        if len(options) != 1:
            continue
        pool = options[0]['pool']
        rows.append({
            'community': ident,
            'only_pool': pool,
            'demand': study.communities[ident]['demand'],
            'served_exposed': fraction(served[ident]),
            'eligible_facilities_in_same_pool': options[0]['eligible_facilities_in_pool'],
            'multiple_facility_labels_do_not_provide_capacity_redundancy': len(options[0]['eligible_facilities_in_pool']) > 1,
        })
    return rows
