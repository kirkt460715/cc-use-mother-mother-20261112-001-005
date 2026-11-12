from collections import defaultdict
from network.routing import Graph, edge_seconds
from network.topology import weak_components
from network.contracts import checksum


def inspect(study):
    findings = []
    endpoints = defaultdict(list)
    for ident, edge in study.edges.items():
        endpoints[(edge['from'], edge['to'])].append(ident)
        if edge['metres'] < 10 and edge['delay_seconds'] > 3600:
            findings.append({'code': 'long_delay_on_short_edge', 'edge': ident, 'severity': 'warning'})
        if not edge['profiles']:
            findings.append({'code': 'edge_no_allowed_profiles', 'edge': ident, 'severity': 'warning'})
    for key, identifiers in sorted(endpoints.items()):
        if len(identifiers) > 1:
            findings.append({'code': 'parallel_directed_edges', 'edges': sorted(identifiers), 'severity': 'info'})
        if (key[1], key[0]) not in endpoints:
            findings.append({'code': 'no_explicit_reverse_edge', 'from': key[0], 'to': key[1], 'severity': 'info'})
    communities_by_node = defaultdict(list)
    for ident, community in study.communities.items():
        communities_by_node[community['node']].append(ident)
        if community['demand'] == 0:
            findings.append({'code': 'zero_demand_community', 'community': ident, 'severity': 'info'})
    facilities_by_pool = defaultdict(list)
    for ident, facility in study.facilities.items():
        facilities_by_pool[facility['pool']].append(ident)
        if facility['capacity'] == 0 and facility['open']:
            findings.append({'code': 'open_zero_capacity_facility', 'facility': ident, 'severity': 'warning'})
        if not facility['profiles']:
            findings.append({'code': 'facility_no_eligible_profiles', 'facility': ident, 'severity': 'warning'})
    for pool, members in sorted(facilities_by_pool.items()):
        if len(members) > 1:
            findings.append({'code': 'shared_capacity_pool', 'pool': pool, 'facilities': sorted(members), 'severity': 'info'})
    for profile in sorted(study.profiles):
        graph = Graph(study, profile)
        components = weak_components(graph)
        for component in components:
            community_ids = [ident for node in component for ident in communities_by_node[node] if study.communities[ident]['profile'] == profile]
            facilities = [ident for ident, row in study.facilities.items() if row['node'] in component and profile in row['profiles'] and row['open']]
            if community_ids and not facilities:
                findings.append({'code': 'component_without_eligible_facility', 'profile': profile, 'communities': sorted(community_ids), 'severity': 'warning'})
    body = {
        'input_digest': study.fingerprint,
        'findings': findings,
        'counts': {severity: sum(row['severity'] == severity for row in findings) for severity in ('warning', 'info')},
        'fatal_schema_errors_previously_rejected': True,
        'warnings_do_not_impute_routes_or_demand': True,
    }
    return {**body, 'quality_digest': checksum(body)}


def cost_breakdown(study, profile_id):
    profile = study.profiles[profile_id]
    rows = []
    for ident, edge in sorted(study.edges.items()):
        walking = (edge['metres'] * profile['seconds_per_km'] + 999) // 1000
        total = edge_seconds(edge, profile)
        rows.append({
            'edge': ident,
            'profile': profile_id,
            'metres': edge['metres'],
            'walking_seconds_rounded_up': walking,
            'fixed_delay_seconds': edge['delay_seconds'],
            'total_seconds': total,
            'delay_dominates_walking': edge['delay_seconds'] > walking,
        })
    return rows
