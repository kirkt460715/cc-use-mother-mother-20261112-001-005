import copy
import json
from pathlib import Path
from fractions import Fraction
import random
import unittest
from network.contracts import Study, checksum
from catchments.pairs import build, exact, fraction, eligibility, verify
from catchments.diagnostics import community_diagnostics, restriction_counterfactual, travel_frontier, route_edge_exposure
from supply.pools import capacities, connections, floating_catchment, audit_index
from supply.competition import allocate, allocate_tier, audit
from supply.integer_plan import conservative_plan, audit_plan
from supply.diagnostics import unused_explanations, competition_components, allocation_travel, priority_summary
from supply.service_balance import pool_service_mix, community_competitors, priority_displacement, single_pool_dependency
from analysis_runs.pipeline import execute, audit as pipeline_audit


class SupplyTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(Path('fixtures/community.json').read_text())
        self.study = Study(self.data)
        self.catchment = build(self.study)
        self.allocation = allocate(self.study, self.catchment)

    def test_eligibility_and_partition(self):
        verify(self.study, self.catchment, replay=True)
        self.assertEqual(len(self.catchment['pairs']) + len(self.catchment['exclusions']), 16)
        community = self.study.communities['nursery']
        facility = self.study.facilities['elder-hub']
        self.assertEqual(eligibility(community, facility), 'service_mismatch')
        self.assertEqual(eligibility(community, {**facility, 'open': False}), 'facility_closed')
        nursery = self.study.facilities['nursery-hub']
        self.assertIsNone(eligibility(community, nursery))
        self.assertEqual(eligibility({**community, 'max_fee_cents': 499}, nursery), 'fee_above_limit')
        self.assertEqual(eligibility(community, {**nursery, 'profiles': []}), 'facility_profile_ineligible')
        pair = next(row for row in self.catchment['pairs'] if row['community'] == 'elder-wheel' and row['facility'] == 'elder-hub')
        self.assertEqual(pair['seconds'], 400)
        self.assertEqual(exact(pair['decay']), Fraction(1, 2))
        self.assertEqual(self.catchment['unique_origin_profile_searches'], 4)

    def test_direction_and_maximum_boundary(self):
        returning = build(self.study, 'return')
        roundtrip = build(self.study, 'roundtrip')
        walk = next(row for row in self.catchment['pairs'] if row['community'] == 'elder-walk' and row['facility'] == 'remote')
        self.assertEqual(walk['seconds'], 300)
        self.assertEqual(exact(walk['decay']), Fraction(1, 4))
        self.assertFalse(any(row['community'] == 'elder-wheel' for row in roundtrip['pairs']))
        for pair in returning['pairs']:
            original = next(row for row in self.catchment['pairs'] if row['community'] == pair['community'] and row['facility'] == pair['facility'])
            self.assertEqual(pair['seconds'], original['seconds'])
        with self.assertRaises(ValueError):
            build(self.study, 'bidirectional')
        damaged = copy.deepcopy(self.catchment)
        damaged['pairs'].pop()
        damaged['catchment_digest'] = checksum({key: value for key, value in damaged.items() if key != 'catchment_digest'})
        with self.assertRaises(ValueError):
            verify(self.study, damaged)

    def test_pool_deduplication(self):
        self.assertEqual(capacities(self.study), {'hub': 10, 'remote': 4})
        links = connections(self.study, self.catchment)
        wheel = next(row for row in links if row['community'] == 'elder-wheel')
        self.assertEqual(wheel['pool'], 'hub')
        self.assertEqual(wheel['eligible_facilities_in_pool'], ['elder-hub', 'elder-mirror'])
        self.assertEqual(wheel['facility'], 'elder-hub')
        walk = next(row for row in links if row['community'] == 'elder-walk' and row['pool'] == 'hub')
        self.assertEqual(walk['facility'], 'elder-mirror')
        index = floating_catchment(self.study, self.catchment)
        checked = audit_index(self.study, self.catchment, index)
        self.assertEqual(exact(checked['demand_weighted_accessibility']), 14)
        hub = next(row for row in index['pools'] if row['pool'] == 'hub')
        self.assertEqual(exact(hub['weighted_competing_demand']), 14)
        self.assertEqual(exact(hub['supply_demand_ratio']), Fraction(5, 7))

    def test_priority_allocation_and_exact_conservation(self):
        report = self.allocation
        checked = audit(self.study, self.catchment, report, replay=True)
        self.assertTrue(checked['valid'])
        self.assertEqual(exact(checked['served']), 14)
        communities = {row['community']: row for row in report['communities']}
        self.assertEqual(exact(communities['elder-wheel']['served']), 8)
        self.assertEqual(exact(communities['isolated']['served']), 0)
        self.assertEqual(exact(communities['elder-walk']['served']), Fraction(26, 5))
        self.assertEqual(exact(communities['nursery']['served']), Fraction(4, 5))
        for row in report['communities']:
            self.assertEqual(exact(row['served']) + exact(row['unserved']), row['demand'])
        for row in report['pools']:
            self.assertEqual(exact(row['used']) + exact(row['unused']), row['capacity'])
        self.assertEqual(report['trace'][0]['priority'], 2)
        self.assertTrue(all(row['communities_satisfied'] or row['pools_exhausted'] for tier in report['trace'] for row in tier['rounds']))

    def test_saturated_community_redistributes_offer(self):
        demands = {'small': 1, 'large': 9}
        links = [
            {'community': 'small', 'pool': 'x', 'decay': fraction(1)},
            {'community': 'small', 'pool': 'y', 'decay': fraction(1)},
            {'community': 'large', 'pool': 'x', 'decay': fraction(1)},
        ]
        allocated, residual, capacity, trace = allocate_tier(demands, {'x': 9, 'y': 9}, links)
        self.assertEqual(residual, {'small': 0, 'large': Fraction(1, 11)})
        self.assertEqual(sum(allocated.values()), Fraction(109, 11))
        self.assertEqual(sum(capacity.values()), Fraction(89, 11))
        self.assertEqual(len(trace), 2)
        self.assertEqual(allocated[('small', 'x')], Fraction(1, 11))
        self.assertEqual(allocated[('small', 'y')], Fraction(10, 11))
        self.assertEqual(allocated[('large', 'x')], Fraction(98, 11))
        self.assertEqual(capacity['x'], Fraction(0))
        self.assertEqual(capacity['y'], Fraction(89, 11))

    def test_random_progressive_offer_invariants(self):
        rng = random.Random(88)
        for trial in range(100):
            demands = {f'c{i}': rng.randrange(10) for i in range(5)}
            pools = {f'p{i}': rng.randrange(10) for i in range(4)}
            links = [{'community': c, 'pool': p, 'decay': fraction(Fraction(rng.randrange(1, 5), 4))} for c in demands for p in pools if rng.random() < .5]
            allocated, residual, remaining, trace = allocate_tier(demands, pools, links)
            self.assertLessEqual(len(trace), len(demands) + len(pools))
            for community, demand in demands.items():
                served = sum(value for (c, p), value in allocated.items() if c == community)
                self.assertEqual(served + residual[community], demand)
                self.assertGreaterEqual(residual[community], 0)
            for pool, capacity in pools.items():
                used = sum(value for (c, p), value in allocated.items() if p == pool)
                self.assertEqual(used + remaining[pool], capacity)
                self.assertGreaterEqual(remaining[pool], 0)
            for link in links:
                self.assertTrue(residual[link['community']] == 0 or remaining[link['pool']] == 0)

    def test_integer_projection(self):
        plan = conservative_plan(self.study, self.catchment, self.allocation)
        checked = audit_plan(self.study, self.catchment, plan)
        self.assertTrue(checked['valid'])
        self.assertEqual(checked['served'], 14)
        self.assertEqual(checked['unserved'], 6)
        self.assertTrue(all(type(row['units']) is int for row in plan['rows']))
        self.assertEqual(sum(row['units'] for row in plan['unused']), 0)
        forged = copy.deepcopy(plan)
        forged['rows'][0]['units'] += 1
        with self.assertRaises(ValueError):
            audit_plan(self.study, self.catchment, forged)
        forged = copy.deepcopy(self.allocation)
        forged['communities'].append(forged['communities'][0])
        forged['allocation_digest'] = checksum({key: value for key, value in forged.items() if key != 'allocation_digest'})
        with self.assertRaises(ValueError):
            audit(self.study, self.catchment, forged)

    def test_index_is_distinct_from_actual_coverage(self):
        index = floating_catchment(self.study, self.catchment)
        scores = {row['community']: exact(row['accessibility']) for row in index['communities']}
        self.assertEqual(scores['elder-wheel'], Fraction(5, 14))
        self.assertEqual(scores['elder-walk'], Fraction(29, 21))
        actual = next(row for row in self.allocation['communities'] if row['community'] == 'elder-wheel')
        self.assertEqual(exact(actual['served']) / actual['demand'], 1)
        self.assertNotEqual(scores['elder-wheel'], 1)
        self.assertGreater(scores['elder-walk'], 1)
        self.assertEqual(scores['isolated'], 0)
        self.assertIn('not_an_actual_service_assignment', index['interpretation'])

    def test_diagnostics_and_shared_services(self):
        diagnoses = community_diagnostics(self.study, self.catchment)
        isolated = next(row for row in diagnoses if row['community'] == 'isolated')
        self.assertEqual(isolated['summary_reason'], 'route_barrier')
        self.assertIsNone(isolated['nearest_seconds'])
        unused = unused_explanations(self.study, self.catchment, self.allocation)
        self.assertTrue(all(row['reason'] == 'capacity_exhausted' for row in unused))
        components = competition_components(self.study, self.catchment)
        self.assertEqual(len(components), 2)
        component = next(row for row in components if 'hub' in row['pools'])
        self.assertEqual(component['demand'], 18)
        self.assertEqual(component['capacity'], 14)
        self.assertEqual(component['capacity_only_shortfall_lower_bound'], 4)
        mix = pool_service_mix(self.study, self.catchment, self.allocation)
        hub = next(row for row in mix if row['pool'] == 'hub')
        self.assertTrue(hub['capacity_shared_between_service_types'])
        self.assertEqual(len(hub['service_mix']), 2)
        competitors = community_competitors(self.study, self.catchment, 'nursery')
        self.assertTrue(all(row['cross_service_competition'] for row in competitors['competitors']))
        dependency = single_pool_dependency(self.study, self.catchment, self.allocation)
        wheel = next(row for row in dependency if row['community'] == 'elder-wheel')
        self.assertTrue(wheel['multiple_facility_labels_do_not_provide_capacity_redundancy'])

    def test_travel_frontier_and_displacement(self):
        travel = allocation_travel(self.allocation)
        self.assertEqual(exact(travel['served']), 14)
        self.assertGreater(exact(travel['mean_seconds']), 0)
        self.assertEqual(priority_summary(self.study, self.allocation)['rows'][0]['priority'], 2)
        displacement = priority_displacement(self.study, self.catchment)
        wheel = next(row for row in displacement['rows'] if row['community'] == 'elder-wheel')
        self.assertGreater(exact(wheel['difference']), 0)
        frontier = travel_frontier(self.study, self.catchment)
        walk = next(row for row in frontier if row['community'] == 'elder-walk')
        self.assertEqual([row['facility'] for row in walk['frontier']], ['elder-mirror'])
        exposure = route_edge_exposure(self.study, self.catchment)
        self.assertTrue(any(row['edge'] == 'AC' for row in exposure))
        barriers = restriction_counterfactual(self.study)
        wheel = next(row for row in barriers if row['community'] == 'elder-wheel' and row['facility'] == 'elder-mirror')
        self.assertEqual(wheel['all_restrictions_ignored_seconds'], 100)
        self.assertEqual(wheel['barriers_on_ignored_route'][0]['reason'], 'steps')

    def test_zero_demand_supply_and_empty_study(self):
        document = self.study.export()
        for row in document['communities']:
            row['demand'] = 0
        study = Study(document)
        result = execute(study)
        self.assertTrue(pipeline_audit(study, result, replay=True)['valid'])
        self.assertEqual(exact(result['overall']['rows'][0]['served']), 0)
        self.assertIsNone(result['overall']['rows'][0]['served_share'])
        self.assertEqual(sum(exact(row['unused']) for row in result['allocation']['pools']), 14)
        self.assertTrue(all(row['reason'] in ('reachable_communities_have_zero_demand', 'no_eligible_reachable_community') for row in unused_explanations(study, result['catchment'], result['allocation'])))
        empty = Study({'version': 1, 'nodes': [], 'edges': [], 'profiles': [], 'communities': [], 'facilities': []})
        result = execute(empty)
        self.assertEqual(result['overall']['rows'], [])
        self.assertTrue(pipeline_audit(empty, result, replay=True)['valid'])


if __name__ == '__main__':
    unittest.main()
