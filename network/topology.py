from collections import deque, defaultdict
from network.routing import Graph


def strongly_connected(graph):
    visited = set()
    finish = []
    for origin in sorted(graph.adjacency):
        if origin in visited:
            continue
        visited.add(origin)
        stack = [(origin, 0)]
        while stack:
            node, index = stack[-1]
            neighbors = graph.adjacency[node]
            if index == len(neighbors):
                finish.append(node)
                stack.pop()
                continue
            stack[-1] = (node, index + 1)
            target = neighbors[index][0]
            if target not in visited:
                visited.add(target)
                stack.append((target, 0))
    reverse = {node: [] for node in graph.adjacency}
    for node, values in graph.adjacency.items():
        for target, cost, edge in values:
            reverse[target].append(node)
    visited.clear()
    groups = []
    for origin in reversed(finish):
        if origin in visited:
            continue
        component = []
        stack = [origin]
        visited.add(origin)
        while stack:
            node = stack.pop()
            component.append(node)
            for target in sorted(reverse[node], reverse=True):
                if target not in visited:
                    visited.add(target)
                    stack.append(target)
        groups.append(sorted(component))
    return sorted(groups, key=lambda values: values[0])


def condensation(graph):
    components = strongly_connected(graph)
    membership = {node: index for index, values in enumerate(components) for node in values}
    links = defaultdict(set)
    for node, values in graph.adjacency.items():
        for target, cost, edge in values:
            first, last = membership[node], membership[target]
            if first != last:
                links[(first, last)].add(edge)
    indegree = {index: 0 for index in range(len(components))}
    adjacency = defaultdict(set)
    for first, last in links:
        adjacency[first].add(last)
        indegree[last] += 1
    queue = deque(sorted(index for index, value in indegree.items() if value == 0))
    order = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for target in sorted(adjacency[node]):
            indegree[target] -= 1
            if not indegree[target]:
                queue.append(target)
    if len(order) != len(components):
        raise AssertionError('SCC condensation must be acyclic')
    return {
        'components': [{'id': index, 'nodes': values} for index, values in enumerate(components)],
        'links': [{'from': first, 'to': last, 'edges': sorted(edges)} for (first, last), edges in sorted(links.items())],
        'topological_order': order,
        'membership': membership,
    }


def weak_components(graph):
    undirected = {node: set() for node in graph.adjacency}
    for node, values in graph.adjacency.items():
        for target, cost, edge in values:
            undirected[node].add(target)
            undirected[target].add(node)
    seen = set()
    groups = []
    for origin in sorted(undirected):
        if origin in seen:
            continue
        queue = [origin]
        seen.add(origin)
        group = []
        while queue:
            node = queue.pop()
            group.append(node)
            for target in sorted(undirected[node]):
                if target not in seen:
                    seen.add(target)
                    queue.append(target)
        groups.append(sorted(group))
    return groups


def profile_topology(study):
    output = []
    for ident in sorted(study.profiles):
        graph = Graph(study, ident)
        strong = strongly_connected(graph)
        weak = weak_components(graph)
        output.append({
            'profile': ident,
            'usable_directed_edges': len(graph.accepted),
            'excluded_edges': {reason: sum(value == reason for value in graph.excluded.values()) for reason in sorted(set(graph.excluded.values()))},
            'strong_components': len(strong),
            'weak_components': len(weak),
            'largest_strong_component': max((len(group) for group in strong), default=0),
            'isolated_nodes': [group[0] for group in weak if len(group) == 1],
            'condensation': condensation(graph),
        })
    return output
