from network.routing import Graph, restriction, edge_seconds
from network.contracts import fields, number


def validate(study, profile_id, route, source, target, reverse=False, prove_shortest=False):
    if type(reverse) is not bool or type(prove_shortest) is not bool:
        raise ValueError('path audit flags must be boolean')
    if source not in study.nodes or target not in study.nodes:
        raise ValueError('unknown path endpoints')
    fields(route, ('nodes', 'edges', 'seconds', 'reverse_graph'))
    nodes, edges = route['nodes'], route['edges']
    if not isinstance(nodes, list) or not isinstance(edges, list) or len(nodes) != len(edges) + 1:
        raise ValueError('path node/edge lengths disagree')
    if not nodes or nodes[0] != source or nodes[-1] != target:
        raise ValueError('path endpoints disagree')
    if len(nodes) != len(set(nodes)):
        raise ValueError('path must be simple')
    if route['reverse_graph'] is not reverse:
        raise ValueError('path graph orientation mismatch')
    profile = study.profiles[profile_id]
    total = 0
    for first, last, ident in zip(nodes, nodes[1:], edges):
        if ident not in study.edges:
            raise ValueError('path contains unknown edge')
        edge = study.edges[ident]
        expected = (edge['to'], edge['from']) if reverse else (edge['from'], edge['to'])
        if (first, last) != expected:
            raise ValueError('path edge direction or continuity mismatch')
        if restriction(edge, profile) is not None:
            raise ValueError('path uses restricted edge')
        total += edge_seconds(edge, profile)
    number(route['seconds'], 'route.seconds', 0, 10**12)
    if total != route['seconds']:
        raise ValueError('path duration differs from edge sum')
    if prove_shortest:
        tree = Graph(study, profile_id, reverse).shortest(source)
        if tree['distances'].get(target) != total:
            raise ValueError('path is feasible but not shortest')
    return {'valid': True, 'seconds': total, 'within_single_journey_threshold': total <= profile['max_seconds'], 'shortest_proven': prove_shortest}


def catchment_routes(study, catchment):
    checked = 0
    for pair in catchment['pairs']:
        community = study.communities[pair['community']]
        facility = study.facilities[pair['facility']]
        for key, reverse in [('outbound_route', False), ('return_route_reversed', True)]:
            route = pair[key]
            if route is None:
                continue
            validate(study, community['profile'], route, community['node'], facility['node'], reverse, True)
            checked += 1
    return {'valid': True, 'paths': checked, 'reverse_routes_are_stored_in_reversed_search_orientation': True}


def work_estimate(study):
    origins = {(row['profile'], row['node']) for row in study.communities.values()}
    pairs = len(study.communities) * len(study.facilities)
    adjacency_scans = 2 * len(origins) * (len(study.nodes) + len(study.edges))
    shared = {}
    for row in study.facilities.values():
        shared.setdefault(row['pool'], []).append(row['id'])
    return {
        'nodes': len(study.nodes),
        'directed_edges': len(study.edges),
        'distinct_origin_profile_searches_both_directions': 2 * len(origins),
        'community_facility_pairs': pairs,
        'linear_graph_scan_upper_proxy': adjacency_scans,
        'priority_allocation_round_bound': len(study.communities) + len(shared) + 1,
        'shared_pool_members': {pool: sorted(members) for pool, members in sorted(shared.items())},
        'large_pair_product_warning': pairs > 1000000,
        'large_search_proxy_warning': adjacency_scans > 100000000,
        'not_a_wall_clock_or_memory_guarantee': True,
    }
