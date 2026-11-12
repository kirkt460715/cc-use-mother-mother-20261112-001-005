from collections import defaultdict
from network.routing import Graph, restriction
from catchments.pairs import eligibility, exact, fraction, verify


def community_diagnostics(study, catchment):
    verify(study, catchment)
    groups = defaultdict(list)
    excluded = defaultdict(list)
    for pair in catchment['pairs']:
        groups[pair['community']].append(pair)
    for row in catchment['exclusions']:
        excluded[row['community']].append(row)
    output = []
    for ident, community in sorted(study.communities.items()):
        options = groups[ident]
        rejected = excluded[ident]
        reasons = defaultdict(int)
        for row in rejected:
            reasons[row['reason']] += 1
        if options:
            reason = 'geographically_covered'
            if not any(study.facilities[row['facility']]['capacity'] > 0 for row in options):
                reason = 'reachable_but_zero_capacity'
        elif not study.facilities:
            reason = 'no_facilities'
        elif reasons['no_directed_route']:
            reason = 'route_barrier'
        elif reasons['travel_time_exceeded']:
            reason = 'beyond_time_threshold'
        else:
            reason = 'no_eligible_facility'
        output.append({
            'community': ident,
            'demand': community['demand'],
            'summary_reason': reason,
            'exclusion_counts': dict(sorted(reasons.items())),
            'reachable_facilities': len(options),
            'reachable_distinct_pools': len({row['pool'] for row in options}),
            'nearest_seconds': min((row['seconds'] for row in options), default=None),
            'nearest_facility': min(options, key=lambda row: (row['seconds'], row['facility']))['facility'] if options else None,
        })
    return output


def restriction_counterfactual(study):
    output = []
    for ident, community in sorted(study.communities.items()):
        profile = study.profiles[community['profile']]
        restricted = Graph(study, profile['id'])
        unrestricted = Graph(study, profile['id'], ignore_restrictions=True)
        first = restricted.shortest(community['node'])
        other = unrestricted.shortest(community['node'])
        for facility_id, facility in sorted(study.facilities.items()):
            if eligibility(community, facility) is not None:
                continue
            actual = first['distances'].get(facility['node'])
            baseline = other['distances'].get(facility['node'])
            if baseline is None or actual == baseline:
                continue
            route = unrestricted.route(other, facility['node'])
            barriers = []
            for edge in route['edges']:
                reason = restriction(study.edges[edge], profile)
                if reason:
                    barriers.append({'edge': edge, 'reason': reason})
            output.append({
                'community': ident,
                'facility': facility_id,
                'restricted_seconds': actual,
                'all_restrictions_ignored_seconds': baseline,
                'barriers_on_ignored_route': barriers,
                'interpretation': 'diagnostic_ignores_closed_and_profile_rules_not_a_feasible_route_or_single_barrier_causal_claim',
            })
    return output


def travel_frontier(study, catchment):
    verify(study, catchment)
    groups = defaultdict(list)
    for pair in catchment['pairs']:
        groups[pair['community']].append(pair)
    rows = []
    for community, options in sorted(groups.items()):
        ordered = sorted(options, key=lambda row: (row['seconds'], -study.facilities[row['facility']]['capacity'], study.facilities[row['facility']]['fee_cents'], row['facility']))
        frontier = []
        for candidate in ordered:
            facility = study.facilities[candidate['facility']]
            dominated = False
            for other in options:
                other_facility = study.facilities[other['facility']]
                weak = other['seconds'] <= candidate['seconds'] and other_facility['fee_cents'] <= facility['fee_cents'] and other_facility['capacity'] >= facility['capacity']
                strict = other['seconds'] < candidate['seconds'] or other_facility['fee_cents'] < facility['fee_cents'] or other_facility['capacity'] > facility['capacity']
                if weak and strict:
                    dominated = True
                    break
            if not dominated:
                frontier.append({'facility': candidate['facility'], 'pool': candidate['pool'], 'seconds': candidate['seconds'], 'fee_cents': facility['fee_cents'], 'pool_capacity': facility['capacity']})
        rows.append({'community': community, 'frontier': frontier, 'shared_pool_capacity_not_additive_across_options': True})
    return rows


def route_edge_exposure(study, catchment):
    verify(study, catchment)
    groups = defaultdict(list)
    for pair in catchment['pairs']:
        route = pair['outbound_route']
        if route is None:
            continue
        for edge in route['edges']:
            groups[edge].append(pair)
    output = []
    for edge, pairs in sorted(groups.items()):
        communities = {row['community'] for row in pairs}
        output.append({
            'edge': edge,
            'community_facility_routes': len(pairs),
            'distinct_communities': len(communities),
            'potential_demand_exposure': sum(study.communities[ident]['demand'] for ident in communities),
            'communities': sorted(communities),
            'facilities': sorted({row['facility'] for row in pairs}),
            'interpretation': 'selected_shortest_path_exposure_not_actual_traffic_or_necessary_bridge',
        })
    return output
