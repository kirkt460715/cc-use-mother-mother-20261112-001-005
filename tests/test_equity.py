import copy
import csv
import io
import json
from pathlib import Path
from fractions import Fraction
import random
import unittest
from network.contracts import Study, checksum
from analysis_runs.pipeline import execute, audit
from analysis_runs.scenarios import modify, pool_capacity_patches, evaluate_cases, capacity_sweep, outage, dependency_scope
from analysis_runs.reports import report, csv_report
from equity.metrics import aggregate, weighted_quantile, weighted_gini, threshold_coverage, rural_urban_gap
from equity.disclosure import protected, public_packet, verify
from equity.comparison import compare, rank_changes, pair_transitions
from equity.query import select, rank_shortfalls, token, untoken
from catchments.pairs import exact, fraction


class EquityTests(unittest.TestCase):
    def setUp(self):
        self.study = Study(json.loads(Path('fixtures/community.json').read_text()))
        self.result = execute(self.study)
        self.rows = self.result['community_metrics']

    def test_coverage_and_capacity_are_different(self):
        overall = aggregate(self.rows, [])['rows'][0]
        self.assertEqual(overall['demand'], 20)
        self.assertEqual(overall['geographic_covered_demand'], 18)
        self.assertEqual(exact(overall['geographic_coverage_share']), Fraction(9, 10))
        self.assertEqual(exact(overall['served']), 14)
        self.assertEqual(exact(overall['unserved']), 6)
        self.assertEqual(exact(overall['served_share']), Fraction(7, 10))
        self.assertEqual(exact(overall['mean_accessibility']), Fraction(7, 10))
        self.assertEqual(exact(overall['nearest_travel_p50']), 100)
        self.assertTrue(overall['travel_quantiles_exclude_unreachable_demand'])
        rural = rural_urban_gap(self.rows)
        self.assertEqual(rural['rural']['demand'], 2)
        self.assertEqual(exact(rural['served_share_gap_rural_minus_urban']), Fraction(-7, 9))
        thresholds = threshold_coverage(self.rows, [0, 100, 400])
        self.assertEqual([row['covered_demand'] for row in thresholds], [0, 10, 18])

    def test_weighted_quantile_and_gini_independent(self):
        rng = random.Random(777)
        for size in range(1, 20):
            values = [(rng.randrange(100), rng.randrange(1, 5)) for _ in range(size)]
            expanded = sorted(value for value, weight in values for _ in range(weight))
            for p in [Fraction(0), Fraction(1, 10), Fraction(1, 2), Fraction(9, 10), Fraction(1)]:
                rank = (p.numerator * len(expanded) + p.denominator - 1) // p.denominator
                expected = expanded[max(0, rank - 1)]
                self.assertEqual(weighted_quantile(values, p), expected)
            total = sum(expanded)
            expected = Fraction(sum(abs(a - b) for a in expanded for b in expanded), 2 * len(expanded) * total) if total else None
            self.assertEqual(weighted_gini(values), expected)
        self.assertIsNone(weighted_quantile([(1, 0)], Fraction(1, 2)))
        self.assertIsNone(weighted_gini([(0, 1)]))
        with self.assertRaises(ValueError):
            weighted_quantile([(1, 1)], 2)
        with self.assertRaises(ValueError):
            weighted_gini([(-1, 1)])

    def test_public_partition_suppression(self):
        hidden = protected(self.rows, ['region'], 2, 1)
        self.assertTrue(all(row['suppressed'] for row in hidden['rows']))
        self.assertFalse(hidden['total_publishable'])
        for row in hidden['rows']:
            self.assertEqual(set(row), {'region', 'suppressed'})
        safe = protected(self.rows, ['region'], 1, 1)
        self.assertFalse(any(row['suppressed'] for row in safe['rows']))
        packet = public_packet(self.rows, self.result['result_digest'], 2, 1)
        self.assertTrue(verify(packet))
        self.assertEqual(packet['reports'][0]['rows'], [{'suppressed': True}])
        self.assertFalse(packet['contains_routes_or_community_ids'])
        forged = copy.deepcopy(packet)
        forged['reports'][0]['rows'][0]['demand'] = 20
        forged['public_digest'] = checksum({key: value for key, value in forged.items() if key != 'public_digest'})
        with self.assertRaises(ValueError):
            verify(forged)
        with self.assertRaises(ValueError):
            protected(self.rows, [], 0, 1)

    def test_query_sorting_filtering_and_cursors(self):
        page = select(self.result, columns=['community', 'demand'], limit=2)
        self.assertEqual(page['total'], 4)
        self.assertEqual(page['rows'][0]['community'], 'elder-walk')
        next_page = select(self.result, columns=['community', 'demand'], limit=2, cursor=page['next_cursor'])
        self.assertIsNone(next_page['next_cursor'])
        self.assertEqual(len(next_page['rows']), 2)
        self.assertEqual(untoken(token({'x': 1})), {'x': 1})
        with self.assertRaises(ValueError):
            select(self.result, columns=['community'], cursor=page['next_cursor'])
        demand = select(self.result, order='demand', descending=True, columns=['community'])
        self.assertEqual(demand['rows'][0]['community'], 'elder-wheel')
        access = select(self.result, order='accessibility', descending=True, columns=['community'])
        self.assertEqual(access['rows'][0]['community'], 'elder-walk')
        selected = select(self.result, filters={'rural': [True]}, columns=['community'])
        self.assertEqual(selected['rows'], [{'community': 'isolated'}])
        self.assertEqual(select(self.result, filters={'rural': [1]})['total'], 0)
        self.assertEqual(rank_shortfalls(self.result)['rows'][0]['community'], 'nursery')
        with self.assertRaises(ValueError):
            select(self.result, columns=['unknown'])
        with self.assertRaises(ValueError):
            select(self.result, limit=True)
        with self.assertRaises(ValueError):
            untoken('!not-base64!')

    def test_pool_capacity_change_is_joint(self):
        patches = pool_capacity_patches(self.study, 'hub', 15)
        self.assertEqual(len(patches), 3)
        variant = modify(self.study, patches)
        self.assertEqual({row['capacity'] for row in variant.facilities.values() if row['pool'] == 'hub'}, {15})
        with self.assertRaises(ValueError):
            modify(self.study, patches[:1])
        after = execute(variant)
        paired = compare(self.result, after)
        self.assertEqual(paired['unmatched'], [])
        self.assertEqual(paired['population_changed_excluded'], [])
        self.assertGreater(sum(exact(row['served_change']) for row in paired['paired']), 0)
        rankings = rank_changes(self.result, after)
        self.assertTrue(rankings['improved'])
        self.assertEqual(rankings['worsened'], [])
        self.assertEqual(pair_transitions(self.result, after)['rows'], [])
        self.assertEqual(dependency_scope(patches), ['supply', 'equity'])
        self.assertEqual(dependency_scope([{'table': 'edges', 'field': 'closed'}]), ['routing', 'catchments', 'supply', 'equity'])

    def test_outage_changes_routes_and_pair_eligibility(self):
        variant = modify(self.study, [{'table': 'edges', 'id': 'AD', 'field': 'closed', 'value': True}])
        after = execute(variant)
        transitions = pair_transitions(self.result, after)
        self.assertTrue(any(row['community'] == 'elder-wheel' and row['transition'] == 'eligibility_lost' for row in transitions['rows']))
        comparison = compare(self.result, after)
        wheel = next(row for row in comparison['paired'] if row['community'] == 'elder-wheel')
        self.assertEqual(exact(wheel['served_change']), 0)
        report = outage(self.study, edge_ids=['AD'])
        self.assertEqual(report['closed_edges'], ['AD'])
        self.assertEqual(report['comparison'], comparison)
        with self.assertRaises(ValueError):
            outage(self.study, edge_ids=['AD', 'AD'])
        with self.assertRaises(ValueError):
            compare(self.result, execute(self.study, 'return'))

    def test_cases_and_population_changes(self):
        cases = [{'name': 'capacity', 'patches': pool_capacity_patches(self.study, 'hub', 20)}]
        result = evaluate_cases(self.study, cases)
        self.assertEqual(result['cases'][0]['name'], 'capacity')
        self.assertNotEqual(result['baseline_digest'], result['cases'][0]['result_digest'])
        with self.assertRaises(ValueError):
            evaluate_cases(self.study, cases * 2)
        changed = modify(self.study, [{'table': 'communities', 'id': 'nursery', 'field': 'demand', 'value': 10}])
        comparison = compare(self.result, execute(changed))
        self.assertEqual(comparison['population_changed_excluded'], ['nursery'])
        sweep = capacity_sweep(self.study, 'hub', [0, 5, 10, 20])
        amounts = [exact(row['served']) for row in sweep['rows']]
        self.assertEqual(amounts, sorted(amounts))
        self.assertTrue(sweep['not_an_optimal_site_selection'])
        with self.assertRaises(ValueError):
            capacity_sweep(self.study, 'hub', [1, 1])

    def test_report_adapters_and_csv(self):
        kinds = ['quality', 'topology', 'coverage-diagnostics', 'barriers', 'frontier', 'route-exposure', 'unused-capacity', 'competition-components', 'planned-travel', 'priority', 'audit', 'pool-service-mix', 'priority-displacement', 'single-pool', 'redundancy', 'route-audit', 'work-estimate']
        for kind in kinds:
            with self.subTest(kind=kind):
                self.assertIsNotNone(report(self.study, self.result, kind))
        cases = [
            ('groups', {'dimensions': ['region']}),
            ('thresholds', {'seconds': [100, 400]}),
            ('communities', {'limit': 2}),
            ('shortfalls', {'limit': 2}),
            ('public', {}),
            ('replacement-routes', {'community': 'nursery', 'facility': 'nursery-hub'}),
            ('nearest', {'community': 'nursery'}),
            ('facility-reach', {'facility': 'remote', 'profile': 'walk'}),
            ('costs', {'profile': 'walk'}),
            ('capacity-sweep', {'pool': 'hub', 'values': [10]}),
            ('outage', {'edge_ids': ['AD']}),
            ('cases', {'cases': []}),
            ('csv', {'dimensions': ['region']}),
            ('matched-profiles', {'profiles': ['walk', 'wheel']}),
            ('threshold-sweep', {'profile': 'walk', 'thresholds': [100]}),
            ('asymmetry', {'profile': 'walk'}),
            ('reference-impacts', {'table': 'nodes', 'id': 'A'}),
            ('edge-disjoint', {'profile': 'walk', 'source': 'A', 'target': 'D'}),
            ('isolation', {'profile': 'walk'}),
            ('competitors', {'community': 'nursery'}),
        ]
        for kind, options in cases:
            with self.subTest(kind=kind):
                self.assertIsNotNone(report(self.study, self.result, kind, options))
        rows = aggregate(self.rows, ['region'])['rows']
        rows[0]['region'] = '=unsafe'
        result = csv_report(rows, ['region'])
        csv_rows = list(csv.reader(io.StringIO(result['content'])))
        self.assertEqual(csv_rows[1][0], "'=unsafe")
        self.assertEqual(len(csv_rows), 3)
        with self.assertRaises(ValueError):
            report(self.study, self.result, 'quality', {'extra': 1})
        with self.assertRaises(ValueError):
            report(self.study, self.result, 'unknown')

    def test_result_integrity_and_zero_denominators(self):
        self.assertTrue(audit(self.study, self.result, replay=True)['valid'])
        damaged = copy.deepcopy(self.result)
        damaged['community_metrics'][0]['demand'] += 1
        with self.assertRaises(ValueError):
            audit(self.study, damaged)
        damaged['result_digest'] = checksum({key: value for key, value in damaged.items() if key != 'result_digest'})
        with self.assertRaises(ValueError):
            audit(self.study, damaged)
        row = copy.deepcopy(self.rows[0])
        row['demand'] = 0
        row['served'] = fraction(0)
        row['unserved'] = fraction(0)
        grouped = aggregate([row], [])['rows'][0]
        self.assertIsNone(grouped['geographic_coverage_share'])
        self.assertIsNone(grouped['served_share'])
        self.assertIsNone(grouped['mean_accessibility'])
        self.assertIsNone(grouped['nearest_travel_p50'])
        with self.assertRaises(ValueError):
            aggregate(self.rows, ['community'])
        with self.assertRaises(ValueError):
            threshold_coverage(self.rows, [10, 10])


if __name__ == '__main__':
    unittest.main()
