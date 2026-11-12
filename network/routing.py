from collections import defaultdict
from fractions import Fraction
import heapq
from network.contracts import number, checksum


def restriction(edge, profile):
    if edge['closed']:
        return 'closed'
    if profile['id'] not in edge['profiles']:
        return 'profile_prohibited'
    if profile['step_free'] and edge['steps']:
        return 'steps'
    if edge['slope_permille'] > profile['max_slope_permille']:
        return 'slope'
    return None


def edge_seconds(edge, profile):
    numerator = edge['metres'] * profile['seconds_per_km']
    walking = (numerator + 999) // 1000
    return walking + edge['delay_seconds']


class Graph:
    def __init__(self, study, profile_id, reverse=False, ignore_restrictions=False):
        self.study = study
        self.profile = study.profiles[profile_id]
        self.reverse = reverse
        self.ignore_restrictions = ignore_restrictions
        self.adjacency = {node: [] for node in study.nodes}
        self.accepted = {}
        self.excluded = {}
        for ident, edge in sorted(study.edges.items()):
            reason = restriction(edge, self.profile)
            if reason is not None and not ignore_restrictions:
                self.excluded[ident] = reason
                continue
            first, last = edge['from'], edge['to']
            if reverse:
                first, last = last, first
            cost = edge_seconds(edge, self.profile)
            self.adjacency[first].append((last, cost, ident))
            self.accepted[ident] = cost
        for values in self.adjacency.values():
            values.sort(key=lambda row: (row[1], row[2], row[0]))
        self.fingerprint = checksum({'study': study.fingerprint, 'profile': profile_id, 'reverse': reverse, 'unrestricted': ignore_restrictions})

    def shortest(self, source, maximum=None, blocked_edges=(), blocked_nodes=()):
        if source not in self.adjacency:
            raise KeyError(source)
        if maximum is not None:
            number(maximum, 'maximum', 0, 10**12)
        blocked_edges = set(blocked_edges)
        blocked_nodes = set(blocked_nodes)
        if blocked_edges - self.study.edges.keys() or blocked_nodes - self.study.nodes.keys():
            raise ValueError('unknown blocked graph element')
        if source in blocked_nodes:
            return {'source': source, 'distances': {}, 'predecessors': {}, 'graph_digest': self.fingerprint, 'settled': []}
        distances = {source: 0}
        predecessors = {}
        settled = set()
        queue = [(0, source)]
        order = []
        while queue:
            distance, node = heapq.heappop(queue)
            if node in settled or distances.get(node) != distance:
                continue
            settled.add(node)
            order.append(node)
            for target, cost, edge in self.adjacency[node]:
                if edge in blocked_edges or target in blocked_nodes:
                    continue
                candidate = distance + cost
                if maximum is not None and candidate > maximum:
                    continue
                current = distances.get(target)
                old_predecessor = predecessors.get(target)
                predecessor = (node, edge)
                if current is None or candidate < current:
                    distances[target] = candidate
                    predecessors[target] = predecessor
                    heapq.heappush(queue, (candidate, target))
                elif candidate == current and target not in settled and (old_predecessor is None or predecessor < old_predecessor):
                    predecessors[target] = predecessor
        return {'source': source, 'distances': distances, 'predecessors': {node: list(pair) for node, pair in predecessors.items()}, 'graph_digest': self.fingerprint, 'settled': order}

    def route(self, tree, destination):
        if tree['graph_digest'] != self.fingerprint:
            raise ValueError('shortest path tree belongs to another graph')
        if destination not in tree['distances']:
            return None
        current = destination
        nodes = [current]
        edges = []
        visited = set()
        while current != tree['source']:
            if current in visited:
                raise ValueError('predecessor cycle')
            visited.add(current)
            previous, edge = tree['predecessors'][current]
            edges.append(edge)
            nodes.append(previous)
            current = previous
        nodes.reverse()
        edges.reverse()
        return {'nodes': nodes, 'edges': edges, 'seconds': tree['distances'][destination], 'reverse_graph': self.reverse}

    def audit_tree(self, tree, maximum=None):
        if tree['graph_digest'] != self.fingerprint:
            raise ValueError('tree graph mismatch')
        expected = self.shortest(tree['source'], maximum)
        if expected != tree:
            raise ValueError('shortest tree replay mismatch')
        for node, distance in tree['distances'].items():
            route = self.route(tree, node)
            if sum(self.accepted[edge] for edge in route['edges']) != distance:
                raise ValueError('path distance does not equal edge costs')
        return {'valid': True, 'reachable_nodes': len(tree['distances'])}


def decay_weight(profile, seconds):
    number(seconds, 'seconds', 0, 10**12)
    if seconds > profile['max_seconds']:
        return Fraction(0)
    for point in profile['decay']:
        if seconds <= point['seconds']:
            return Fraction(point['weight_ppm'], 1000000)
    raise AssertionError('validated decay curve must cover threshold')
