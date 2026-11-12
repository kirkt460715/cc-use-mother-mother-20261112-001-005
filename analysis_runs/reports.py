import csv
import io
from network.contracts import fields, canonical
from network.quality import inspect, cost_breakdown
from network.topology import profile_topology
from catchments.diagnostics import community_diagnostics, restriction_counterfactual, travel_frontier, route_edge_exposure
from catchments.alternatives import replacement_routes, nearest, reverse_reachability
from supply.diagnostics import unused_explanations, competition_components, allocation_travel, priority_summary
from equity.metrics import aggregate, threshold_coverage
from equity.disclosure import public_packet
from equity.query import select, rank_shortfalls
from analysis_runs.pipeline import audit
from analysis_runs.scenarios import capacity_sweep, outage, evaluate_cases, dependency_scope


def report(study, result, kind, options=None):
    options = {} if options is None else options
    if not isinstance(options, dict):
        raise ValueError('report options must be object')
    audit(study, result)
    catchment = result['catchment']
    allocation = result['allocation']
    from supply.service_balance import pool_service_mix, community_competitors, priority_displacement, single_pool_dependency
    from network.resilience import edge_disjoint_paths, facility_redundancy, isolation_sets
    from network.path_audit import catchment_routes, work_estimate
    simple = {
        'route-audit': lambda: catchment_routes(study, catchment),
        'work-estimate': lambda: work_estimate(study),
        'redundancy': lambda: facility_redundancy(study, catchment),
        'pool-service-mix': lambda: pool_service_mix(study, catchment, allocation),
        'priority-displacement': lambda: priority_displacement(study, catchment),
        'single-pool': lambda: single_pool_dependency(study, catchment, allocation),
        'quality': lambda: inspect(study),
        'topology': lambda: profile_topology(study),
        'coverage-diagnostics': lambda: community_diagnostics(study, catchment),
        'barriers': lambda: restriction_counterfactual(study),
        'frontier': lambda: travel_frontier(study, catchment),
        'route-exposure': lambda: route_edge_exposure(study, catchment),
        'unused-capacity': lambda: unused_explanations(study, catchment, allocation),
        'competition-components': lambda: competition_components(study, catchment),
        'planned-travel': lambda: allocation_travel(allocation),
        'priority': lambda: priority_summary(study, allocation),
        'audit': lambda: audit(study, result, replay=True),
    }
    if kind in simple:
        fields(options, ())
        return simple[kind]()
    if kind == 'matched-profiles':
        from catchments.profile_analysis import matched_origin_profiles
        fields(options, ('profiles',))
        return matched_origin_profiles(study, options['profiles'])
    if kind == 'threshold-sweep':
        from catchments.profile_analysis import threshold_sweep
        fields(options, ('profile', 'thresholds'))
        return threshold_sweep(study, options['profile'], options['thresholds'])
    if kind == 'asymmetry':
        from catchments.profile_analysis import directed_asymmetry
        fields(options, ('profile',))
        return directed_asymmetry(study, options['profile'])
    if kind == 'reference-impacts':
        from network.corrections import reference_impacts
        fields(options, ('table', 'id'))
        return reference_impacts(study, options['table'], options['id'])
    if kind == 'edge-disjoint':
        fields(options, ('profile', 'source', 'target'), ('maximum_paths',))
        return edge_disjoint_paths(study, options['profile'], options['source'], options['target'], options.get('maximum_paths', 10))
    if kind == 'isolation':
        fields(options, ('profile',))
        return isolation_sets(study, options['profile'])
    if kind == 'competitors':
        fields(options, ('community',))
        return community_competitors(study, catchment, options['community'])
    if kind == 'groups':
        fields(options, ('dimensions',))
        return aggregate(result['community_metrics'], options['dimensions'])
    if kind == 'thresholds':
        fields(options, ('seconds',))
        return threshold_coverage(result['community_metrics'], options['seconds'])
    if kind == 'communities':
        fields(options, (), ('filters', 'columns', 'order', 'descending', 'limit', 'cursor'))
        return select(result, **options)
    if kind == 'shortfalls':
        fields(options, (), ('limit',))
        return rank_shortfalls(result, options.get('limit', 20))
    if kind == 'public':
        fields(options, (), ('minimum_communities', 'minimum_demand'))
        return public_packet(result['community_metrics'], result['result_digest'], **options)
    if kind == 'replacement-routes':
        fields(options, ('community', 'facility'))
        return replacement_routes(study, options['community'], options['facility'])
    if kind == 'nearest':
        fields(options, ('community',), ('limit',))
        return nearest(study, options['community'], options.get('limit', 5), result['direction'])
    if kind == 'facility-reach':
        fields(options, ('facility', 'profile'))
        return reverse_reachability(study, options['facility'], options['profile'])
    if kind == 'costs':
        fields(options, ('profile',))
        return cost_breakdown(study, options['profile'])
    if kind == 'capacity-sweep':
        fields(options, ('pool', 'values'))
        return capacity_sweep(study, options['pool'], options['values'], result['direction'], result['priorities'])
    if kind == 'outage':
        fields(options, (), ('edge_ids', 'facility_ids'))
        return outage(study, direction=result['direction'], priorities=result['priorities'], **options)
    if kind == 'cases':
        fields(options, ('cases',))
        return evaluate_cases(study, options['cases'], result['direction'], result['priorities'])
    if kind == 'csv':
        fields(options, ('dimensions',))
        rows = aggregate(result['community_metrics'], options['dimensions'])['rows']
        return csv_report(rows, options['dimensions'])
    raise ValueError('unknown report kind')


def csv_report(rows, dimensions):
    measures = ['communities', 'demand', 'geographic_covered_demand', 'geographic_coverage_share', 'served', 'unserved', 'served_share', 'mean_accessibility', 'accessibility_gini', 'nearest_travel_p50', 'nearest_travel_p90', 'travel_quantiles_exclude_unreachable_demand', 'zero_demand_communities']
    columns = dimensions + measures
    output = io.StringIO(newline='')
    writer = csv.writer(output, lineterminator='\n')
    writer.writerow(columns)
    for row in rows:
        if set(row) != set(columns):
            raise ValueError('CSV report schema mismatch')
        values = []
        for key in columns:
            value = row[key]
            if value is None:
                value = ''
            elif isinstance(value, dict):
                value = canonical(value)
            elif type(value) is bool:
                value = 'true' if value else 'false'
            elif isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
                value = "'" + value
            values.append(value)
        writer.writerow(values)
    return {'rows': len(rows), 'content': output.getvalue(), 'access': 'restricted_without_small_group_protection', 'rational_cells': 'JSON numerator and denominator'}
