import copy
import json
from pathlib import Path
from fractions import Fraction
import random
import tempfile
import unittest
from network.contracts import Study, ContractError, checksum
from network.routing import Graph, restriction, edge_seconds, decay_weight
from network.topology import strongly_connected, condensation, weak_components, profile_topology
from network.cache import RouteCache
from network.quality import inspect, cost_breakdown
from network.corrections import apply, differences, reference_impacts
from network.path_audit import validate, catchment_routes, work_estimate
from network.resilience import edge_disjoint_paths, facility_redundancy, isolation_sets
from catchments.pairs import build
from catchments.profile_analysis import matched_origin_profiles, threshold_sweep, directed_asymmetry
from catchments.alternatives import replacement_routes, nearest, reverse_reachability


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(Path('fixtures/community.json').read_text())
        self.study = Study(self.data)
        self.walk = Graph(self.study, 'walk')
        self.wheel = Graph(self.study, 'wheel')

    def test_mobility_route_and_costs(self):
        tree = self.walk.shortest('A')
        self.assertEqual(tree['distances']['C'], 50)
        self.assertEqual(tree['distances']['D'], 150)
        self.assertEqual(tree['distances']['E'], 250)
        self.assertNotIn('F', tree['distances'])
        route = self.walk.route(tree, 'D')
        self.assertEqual(route['edges'], ['AC', 'CD'])
        self.assertEqual(route['nodes'], ['A', 'C', 'D'])
        self.assertTrue(validate(self.study, 'walk', route, 'A', 'D', prove_shortest=True)['valid'])
        wheel = self.wheel.shortest('A')
        self.assertEqual(wheel['distances']['D'], 400)
        self.assertEqual(wheel['distances']['C'], 400)
        self.assertEqual(wheel['distances']['E'], 600)
        self.assertNotIn('AC', self.wheel.accepted)
        self.assertEqual(self.wheel.excluded['AC'], 'steps')
        self.assertEqual(self.wheel.audit_tree(wheel)['reachable_nodes'], 5)

    def test_time_limits_and_same_node(self):
        tree = self.walk.shortest('A', 100)
        self.assertEqual(set(tree['distances']), {'A', 'B', 'C'})
        self.assertIsNone(self.walk.route(tree, 'D'))
        self.assertEqual(self.walk.route(tree, 'A')['edges'], [])
        self.assertTrue(self.walk.audit_tree(tree, 100)['valid'])
        self.assertEqual(self.walk.shortest('A', 0)['distances'], {'A': 0})
        with self.assertRaises(ValueError):
            self.walk.shortest('A', -1)
        with self.assertRaises(KeyError):
            self.walk.shortest('unknown')
        with self.assertRaises(ValueError):
            self.walk.route(self.wheel.shortest('A'), 'D')

    def test_dijkstra_against_floyd_warshall(self):
        rng = random.Random(209)
        for trial in range(20):
            document = copy.deepcopy(self.data)
            document['edges'] = []
            for first in 'ABCDE':
                for last in 'ABCDE':
                    if first != last and rng.random() < .4:
                        document['edges'].append({'id': first + last, 'from': first, 'to': last, 'metres': rng.randrange(1, 200), 'delay_seconds': rng.randrange(10), 'steps': 0, 'slope_permille': 0, 'closed': False, 'profiles': ['walk', 'wheel']})
            study = Study(document)
            graph = Graph(study, 'walk')
            nodes = sorted(study.nodes)
            infinity = 10**10
            distances = {(a, b): 0 if a == b else infinity for a in nodes for b in nodes}
            for edge in study.edges.values():
                key = edge['from'], edge['to']
                distances[key] = min(distances[key], edge_seconds(edge, study.profiles['walk']))
            for middle in nodes:
                for a in nodes:
                    for b in nodes:
                        distances[(a, b)] = min(distances[(a, b)], distances[(a, middle)] + distances[(middle, b)])
            for source in nodes:
                tree = graph.shortest(source)
                expected = {target: distances[(source, target)] for target in nodes if distances[(source, target)] < infinity}
                self.assertEqual(tree['distances'], expected)
                for target in expected:
                    self.assertTrue(validate(study, 'walk', graph.route(tree, target), source, target)['valid'])

    def test_directed_and_parallel_edges(self):
        document = copy.deepcopy(self.data)
        document['edges'] = [edge for edge in document['edges'] if edge['id'] in ('AB', 'BC')]
        document['edges'].append({**document['edges'][0], 'id': 'fast', 'metres': 20})
        study = Study(document)
        forward = Graph(study, 'walk')
        reverse = Graph(study, 'walk', reverse=True)
        self.assertEqual(forward.shortest('A')['distances']['C'], 120)
        self.assertNotIn('A', forward.shortest('C')['distances'])
        self.assertEqual(reverse.shortest('C')['distances']['A'], 120)
        route = reverse.route(reverse.shortest('C'), 'A')
        self.assertEqual(route['edges'], ['BC', 'fast'])
        self.assertTrue(validate(study, 'walk', route, 'C', 'A', reverse=True, prove_shortest=True)['valid'])
        blocked = forward.shortest('A', blocked_edges=['fast'])
        self.assertEqual(blocked['distances']['C'], 200)
        blocked_node = forward.shortest('A', blocked_nodes=['B'])
        self.assertEqual(blocked_node['distances'], {'A': 0})
        self.assertEqual(forward.shortest('A', blocked_nodes=['A'])['distances'], {})

    def test_restriction_precedence_and_decay(self):
        profile = self.study.profiles['wheel']
        edge = self.study.edges['AC']
        self.assertEqual(restriction(edge, profile), 'steps')
        self.assertEqual(restriction({**edge, 'closed': True}, profile), 'closed')
        self.assertEqual(restriction({**edge, 'profiles': ['walk']}, profile), 'profile_prohibited')
        self.assertEqual(restriction({**edge, 'steps': 0, 'slope_permille': 31}, profile), 'slope')
        self.assertEqual(decay_weight(profile, 0), 1)
        self.assertEqual(decay_weight(profile, 200), 1)
        self.assertEqual(decay_weight(profile, 201), Fraction(1, 2))
        self.assertEqual(decay_weight(profile, 500), Fraction(1, 2))
        self.assertEqual(decay_weight(profile, 501), 0)
        small = {**edge, 'metres': 1, 'delay_seconds': 3}
        self.assertEqual(edge_seconds(small, {**profile, 'seconds_per_km': 1}), 4)

    def test_components_and_redundancy(self):
        components = strongly_connected(self.walk)
        self.assertEqual(components, [['A', 'B', 'C', 'D', 'E'], ['F']])
        self.assertEqual(weak_components(self.walk), components)
        dag = condensation(self.walk)
        self.assertEqual(len(dag['topological_order']), 2)
        self.assertEqual(dag['links'], [])
        report = edge_disjoint_paths(self.study, 'walk', 'A', 'D')
        self.assertEqual(report['count'], 2)
        from itertools import combinations
        others = [node for node in self.study.nodes if node not in ('A', 'D')]
        cuts = []
        for size in range(len(others) + 1):
            for subset in combinations(others, size):
                side = set(subset) | {'A'}
                cuts.append(sum(edge['from'] in side and edge['to'] not in side for edge in self.study.edges.values()))
        self.assertEqual(report['count'], min(cuts))
        used = [edge for route in report['paths'] for edge in route['edges']]
        self.assertEqual(len(used), len(set(used)))
        self.assertEqual(edge_disjoint_paths(self.study, 'walk', 'A', 'F')['count'], 0)
        self.assertTrue(edge_disjoint_paths(self.study, 'walk', 'A', 'A')['same_node'])
        self.assertTrue(isolation_sets(self.study, 'walk')['travel_threshold_ignored'])
        topology = profile_topology(self.study)
        self.assertEqual(topology[0]['isolated_nodes'], ['F'])

    def test_cache_exact_context_and_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            with RouteCache(Path(directory) / 'routes.sqlite3') as cache:
                first = cache.get(self.study, 'walk', 'A')
                second = cache.get(self.study, 'walk', 'A', cached_only=True)
                self.assertFalse(first['cached'])
                self.assertTrue(second['cached'])
                self.assertEqual(first['tree'], second['tree'])
                self.assertEqual(cache.statistics()['rows'][0]['hits'], 1)
                self.assertTrue(cache.audit()['valid'])
                with self.assertRaises(KeyError):
                    cache.get(self.study, 'walk', 'A', maximum=100, cached_only=True)
                bounded = cache.get(self.study, 'walk', 'A', maximum=100)
                self.assertNotEqual(first['key'], bounded['key'])
                warm = cache.prewarm(self.study)
                self.assertEqual(len(warm['routes']), 8)
                self.assertTrue(all(row['cached'] for row in cache.prewarm(self.study)['routes']))
                self.assertTrue(cache.audit()['valid'])
                cache.db.execute("UPDATE route_trees SET body='{}' WHERE key=?", (first['key'],))
                with self.assertRaises(ValueError):
                    cache.get(self.study, 'walk', 'A')
                self.assertGreater(cache.evict(self.study.fingerprint)['removed_trees'], 0)
                self.assertEqual(cache.audit()['route_trees'], 0)

    def test_schema_rejects_bad_values(self):
        mutations = [
            lambda d: d.update(version=True),
            lambda d: d['nodes'][0].update(rural=0),
            lambda d: d['edges'][0].update(metres=0),
            lambda d: d['edges'][0].update(to='missing'),
            lambda d: d['edges'][0].update(to='A'),
            lambda d: d['profiles'][0].update(seconds_per_km=0),
            lambda d: d['profiles'][0]['decay'][-1].update(seconds=299),
            lambda d: d['profiles'][0]['decay'][1].update(weight_ppm=1000001),
            lambda d: d['communities'][0].update(demand=True),
            lambda d: d['communities'][0].update(profile='missing'),
            lambda d: d['facilities'][0].update(capacity=11),
            lambda d: d['facilities'][0].update(profiles=['walk', 'walk']),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                document = copy.deepcopy(self.data)
                mutation(document)
                with self.assertRaises(ContractError):
                    Study(document)

    def test_atomic_corrections(self):
        record = {**self.data['edges'][0], 'metres': 120}
        operations = [{'table': 'edges', 'id': 'AB', 'action': 'replace', 'record': record}]
        revised, evidence = apply(self.study, operations)
        self.assertEqual(revised.edges['AB']['metres'], 120)
        self.assertEqual(self.study.edges['AB']['metres'], 100)
        self.assertTrue(evidence['joint_referential_validation'])
        self.assertEqual(differences(self.study, revised)['operations'], operations)
        impacts = reference_impacts(self.study, 'nodes', 'A')
        self.assertTrue(impacts['deletion_requires_joint_corrections'])
        with self.assertRaises(ValueError):
            apply(self.study, [{'table': 'nodes', 'id': 'A', 'action': 'remove'}])
        with self.assertRaises(ValueError):
            apply(self.study, operations * 2)
        with self.assertRaises(ValueError):
            apply(self.study, [{'table': 'edges', 'id': 'AB', 'action': 'add', 'record': record}])
        exported = self.study.export()
        exported['nodes'][0]['region'] = 'changed'
        self.assertNotEqual(checksum(exported), self.study.fingerprint)
        self.assertEqual(self.study.nodes['A']['region'], 'east')

    def test_route_diagnostics(self):
        catchment = build(self.study)
        self.assertTrue(catchment_routes(self.study, catchment)['valid'])
        report = replacement_routes(self.study, 'elder-wheel', 'elder-hub')
        self.assertEqual(report['baseline']['seconds'], 400)
        self.assertEqual(report['replacements'][0]['replacement']['seconds'], 600)
        self.assertFalse(report['replacements'][0]['within_threshold'])
        nearest_report = nearest(self.study, 'elder-wheel')
        self.assertEqual(nearest_report['rows'][0]['seconds'], 400)
        reverse = reverse_reachability(self.study, 'elder-hub', 'wheel')
        self.assertTrue(reverse['rows'][0]['within_threshold'])
        redundancy = facility_redundancy(self.study, catchment)
        nursery = next(row for row in redundancy if row['community'] == 'nursery')
        self.assertEqual(nursery['independent_capacity_pools'], 1)
        self.assertFalse(nursery['survives_one_pool_loss_geographically'])
        self.assertEqual(work_estimate(self.study)['community_facility_pairs'], 16)

    def test_profile_comparisons_and_quality(self):
        comparisons = matched_origin_profiles(self.study, ['walk', 'wheel'])
        row = next(row for row in comparisons['rows'] if row['origin'] == 'A' and row['facility'] == 'elder-hub')
        self.assertEqual([profile['seconds'] for profile in row['profiles']], [150, 400])
        sweep = threshold_sweep(self.study, 'wheel', [0, 399, 400, 600])
        self.assertEqual([row['covered_demand'] for row in sweep['rows']], [0, 0, 8, 8])
        self.assertEqual(len(directed_asymmetry(self.study, 'wheel')['rows']), 4)
        quality = inspect(self.study)
        self.assertIn('shared_capacity_pool', {row['code'] for row in quality['findings']})
        costs = cost_breakdown(self.study, 'wheel')
        self.assertEqual(next(row for row in costs if row['edge'] == 'AB')['total_seconds'], 200)
        forged = self.walk.route(self.walk.shortest('A'), 'D')
        forged['seconds'] += 1
        with self.assertRaises(ValueError):
            validate(self.study, 'walk', forged, 'A', 'D')


if __name__ == '__main__':
    unittest.main()
