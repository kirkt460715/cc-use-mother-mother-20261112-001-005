from collections import defaultdict, deque
from network.routing import Graph
from network.contracts import number


def edge_disjoint_paths(study, profile_id, source, target, maximum_paths=10):
    number(maximum_paths, 'maximum_paths', 1, 100)
    graph = Graph(study, profile_id)
    if source not in graph.adjacency or target not in graph.adjacency:
        raise KeyError('unknown node')
    if source == target:
        return {'paths': [], 'count': None, 'same_node': True, 'thresholds_ignored': True}
    adjacency = defaultdict(list)
    residual = {}
    for node, values in graph.adjacency.items():
        for destination, cost, ident in values:
            forward = ident, 1
            backward = ident, -1
            adjacency[node].append((destination, forward))
            adjacency[destination].append((node, backward))
            residual[forward] = 1
            residual[backward] = 0
    flow = 0
    while flow < maximum_paths:
        parents = {source: None}
        queue = deque([source])
        while queue and target not in parents:
            node = queue.popleft()
            for destination, key in sorted(adjacency[node], key=lambda row: (row[0], row[1])):
                if residual[key] > 0 and destination not in parents:
                    parents[destination] = node, key
                    queue.append(destination)
        if target not in parents:
            break
        current = target
        while current != source:
            previous, key = parents[current]
            residual[key] -= 1
            residual[(key[0], -key[1])] += 1
            current = previous
        flow += 1
    used = {ident for ident in graph.accepted if residual[(ident, 1)] == 0}
    paths = []
    for _ in range(flow):
        parents = {source: None}
        queue = deque([source])
        while queue and target not in parents:
            node = queue.popleft()
            for destination, cost, ident in graph.adjacency[node]:
                if ident in used and destination not in parents:
                    parents[destination] = node, ident
                    queue.append(destination)
        if target not in parents:
            raise AssertionError('integral flow cannot be decomposed')
        edges = []
        nodes = [target]
        current = target
        while current != source:
            previous, edge = parents[current]
            edges.append(edge)
            nodes.append(previous)
            used.remove(edge)
            current = previous
        edges.reverse()
        nodes.reverse()
        paths.append({'edges': edges, 'nodes': nodes, 'seconds': sum(graph.accepted[edge] for edge in edges)})
    return {
        'paths': paths,
        'count': flow,
        'capped_at_requested_limit': flow == maximum_paths,
        'same_node': False,
        'thresholds_ignored': True,
        'scope': 'directed_edge_disjoint_topological_redundancy_not_travel_threshold_feasibility_or_node_disjointness',
    }


def facility_redundancy(study, catchment):
    groups = defaultdict(list)
    for row in catchment['pairs']:
        groups[row['community']].append(row)
    output = []
    for ident, community in sorted(study.communities.items()):
        facilities = {row['facility'] for row in groups[ident]}
        pools = {row['pool'] for row in groups[ident]}
        sites = {study.facilities[facility]['node'] for facility in facilities}
        output.append({
            'community': ident,
            'facility_labels': len(facilities),
            'distinct_sites': len(sites),
            'independent_capacity_pools': len(pools),
            'survives_one_facility_closure_geographically': len(facilities) >= 2,
            'survives_one_pool_loss_geographically': len(pools) >= 2,
            'survives_one_site_loss_geographically': len(sites) >= 2,
            'no_capacity_sufficiency_claim': True,
        })
    return output


def isolation_sets(study, profile_id):
    graph = Graph(study, profile_id)
    communities = [row for row in study.communities.values() if row['profile'] == profile_id]
    origins = sorted({row['node'] for row in communities})
    reachable = {origin: set(graph.shortest(origin)['distances']) for origin in origins}
    rows = []
    for origin in origins:
        row = {
            'origin': origin,
            'communities': sorted(row['id'] for row in communities if row['node'] == origin),
            'reachable_nodes': sorted(reachable[origin]),
            'cannot_reach_origins': sorted(other for other in origins if other not in reachable[origin]),
            'mutually_reachable_origins': sorted(other for other in origins if other in reachable[origin] and origin in reachable[other]),
        }
        rows.append(row)
    return {'profile': profile_id, 'rows': rows, 'travel_threshold_ignored': True}
