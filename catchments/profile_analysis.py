from collections import defaultdict
from fractions import Fraction
from network.contracts import checksum, Study, number
from network.routing import Graph, decay_weight
from catchments.pairs import fraction, exact, eligibility


def matched_origin_profiles(study, profile_ids):
    if not isinstance(profile_ids, list) or not profile_ids or len(profile_ids) != len(set(profile_ids)):
        raise ValueError('profile comparison requires nonempty unique ids')
    if set(profile_ids) - study.profiles.keys():
        raise ValueError('unknown profile')
    graphs = {ident: Graph(study, ident) for ident in profile_ids}
    origins = sorted({row['node'] for row in study.communities.values()})
    output = []
    for origin in origins:
        trees = {ident: graph.shortest(origin) for ident, graph in graphs.items()}
        for facility_id, facility in sorted(study.facilities.items()):
            values = []
            for profile in profile_ids:
                seconds = trees[profile]['distances'].get(facility['node'])
                allowed = facility['open'] and profile in facility['profiles']
                threshold = study.profiles[profile]['max_seconds']
                covered = allowed and seconds is not None and seconds <= threshold
                values.append({
                    'profile': profile,
                    'seconds': seconds,
                    'facility_profile_allowed': allowed,
                    'within_own_threshold': covered,
                    'decay': fraction(decay_weight(study.profiles[profile], seconds)) if covered else fraction(0),
                })
            output.append({'origin': origin, 'facility': facility_id, 'profiles': values})
    return {'input_digest': study.fingerprint, 'rows': output, 'service_and_fee_eligibility_not_applied': True, 'scope': 'matched_physical_origins_for_mobility_profile_comparison'}


def threshold_sweep(study, profile_id, thresholds):
    if profile_id not in study.profiles:
        raise KeyError(profile_id)
    if not isinstance(thresholds, list) or len(thresholds) > 100 or len(thresholds) != len(set(thresholds)):
        raise ValueError('threshold sweep requires bounded unique values')
    for threshold in thresholds:
        number(threshold, 'threshold', 0, 86400)
    graph = Graph(study, profile_id)
    communities = [row for row in study.communities.values() if row['profile'] == profile_id]
    trees = {node: graph.shortest(node) for node in {row['node'] for row in communities}}
    nearest = {}
    for community in communities:
        candidates = []
        for facility in study.facilities.values():
            if eligibility(community, facility):
                continue
            seconds = trees[community['node']]['distances'].get(facility['node'])
            if seconds is not None:
                candidates.append(seconds)
        nearest[community['id']] = min(candidates, default=None)
    total = sum(row['demand'] for row in communities)
    rows = []
    for threshold in sorted(thresholds):
        covered = sum(row['demand'] for row in communities if nearest[row['id']] is not None and nearest[row['id']] <= threshold)
        rows.append({'threshold_seconds': threshold, 'demand': total, 'covered_demand': covered, 'coverage_share': fraction(Fraction(covered, total)) if total else None})
    return {'profile': profile_id, 'rows': rows, 'capacity_competition_not_applied': True, 'thresholds_can_exceed_original_profile_limit': True}


def directed_asymmetry(study, profile_id):
    if profile_id not in study.profiles:
        raise KeyError(profile_id)
    forward = Graph(study, profile_id)
    reverse = Graph(study, profile_id, reverse=True)
    origins = sorted({row['node'] for row in study.communities.values() if row['profile'] == profile_id})
    output = []
    for origin in origins:
        out = forward.shortest(origin)
        back = reverse.shortest(origin)
        for facility in sorted(study.facilities.values(), key=lambda row: row['id']):
            if not facility['open'] or profile_id not in facility['profiles']:
                continue
            first = out['distances'].get(facility['node'])
            last = back['distances'].get(facility['node'])
            output.append({
                'origin': origin,
                'facility': facility['id'],
                'outbound_seconds': first,
                'return_seconds': last,
                'one_direction_only': (first is None) != (last is None),
                'return_minus_outbound_seconds': last - first if first is not None and last is not None else None,
            })
    return {'profile': profile_id, 'rows': output, 'threshold_ignored': True}
