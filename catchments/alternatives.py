from network.routing import Graph
from network.contracts import number
from catchments.pairs import eligibility


def replacement_routes(study, community_id, facility_id):
    community = study.communities[community_id]
    facility = study.facilities[facility_id]
    reason = eligibility(community, facility)
    if reason:
        raise ValueError('facility is ineligible: ' + reason)
    graph = Graph(study, community['profile'])
    original_tree = graph.shortest(community['node'])
    original = graph.route(original_tree, facility['node'])
    if original is None:
        return {'community': community_id, 'facility': facility_id, 'baseline': None, 'replacements': []}
    rows = []
    for edge in original['edges']:
        tree = graph.shortest(community['node'], blocked_edges=[edge])
        route = graph.route(tree, facility['node'])
        rows.append({
            'removed_edge': edge,
            'replacement': route,
            'extra_seconds': None if route is None else route['seconds'] - original['seconds'],
            'within_threshold': route is not None and route['seconds'] <= graph.profile['max_seconds'],
        })
    return {'community': community_id, 'facility': facility_id, 'baseline': original, 'replacements': rows, 'single_edge_failures_only': True}


def nearest(study, community_id, limit=5, direction='outbound'):
    number(limit, 'limit', 1, 1000)
    if direction not in ('outbound', 'return', 'roundtrip'):
        raise ValueError('unknown direction')
    community = study.communities[community_id]
    forward = Graph(study, community['profile'])
    reverse = Graph(study, community['profile'], reverse=True)
    first = forward.shortest(community['node'])
    last = reverse.shortest(community['node'])
    rows = []
    for ident, facility in study.facilities.items():
        if eligibility(community, facility):
            continue
        out = first['distances'].get(facility['node'])
        back = last['distances'].get(facility['node'])
        travel = out if direction == 'outbound' else back
        if direction == 'roundtrip':
            travel = out + back if out is not None and back is not None else None
        if travel is None:
            continue
        rows.append({
            'facility': ident,
            'pool': facility['pool'],
            'seconds': travel,
            'within_threshold': travel <= forward.profile['max_seconds'],
            'capacity': facility['capacity'],
            'fee_cents': facility['fee_cents'],
        })
    rows.sort(key=lambda row: (row['seconds'], row['facility']))
    return {'community': community_id, 'direction': direction, 'rows': rows[:limit], 'reachable_eligible_facilities': len(rows)}


def reverse_reachability(study, facility_id, profile_id):
    facility = study.facilities[facility_id]
    graph = Graph(study, profile_id, reverse=True)
    tree = graph.shortest(facility['node'], graph.profile['max_seconds'])
    rows = []
    for ident, community in sorted(study.communities.items()):
        if community['profile'] != profile_id or eligibility(community, facility):
            continue
        seconds = tree['distances'].get(community['node'])
        rows.append({
            'community': ident,
            'outbound_seconds': seconds,
            'within_threshold': seconds is not None,
            'demand': community['demand'],
        })
    return {'facility': facility_id, 'profile': profile_id, 'rows': rows, 'orientation': 'reverse_search_from_facility_equals_outbound_community_search'}
