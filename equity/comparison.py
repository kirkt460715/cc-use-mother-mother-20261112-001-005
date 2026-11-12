from collections import defaultdict
from fractions import Fraction
from catchments.pairs import exact, fraction
from network.contracts import checksum


def compare(before, after):
    if before['direction'] != after['direction']:
        raise ValueError('comparison requires same journey orientation')
    for result in (before, after):
        body = {key: value for key, value in result.items() if key != 'result_digest'}
        if checksum(body) != result.get('result_digest'):
            raise ValueError('comparison input checksum mismatch')
    old = {row['community']: row for row in before['community_metrics']}
    new = {row['community']: row for row in after['community_metrics']}
    paired = []
    unmatched = []
    changed_population = []
    for ident in sorted(old.keys() | new.keys()):
        if ident not in old or ident not in new:
            unmatched.append({'community': ident, 'side': 'added' if ident in new else 'removed'})
            continue
        left, right = old[ident], new[ident]
        if any(left[key] != right[key] for key in ('demand', 'profile', 'service', 'region', 'rural')):
            changed_population.append(ident)
            continue
        nearest_change = None
        if left['nearest_seconds'] is not None and right['nearest_seconds'] is not None:
            nearest_change = right['nearest_seconds'] - left['nearest_seconds']
        paired.append({
            'community': ident,
            'demand': left['demand'],
            'region': left['region'],
            'coverage_gained': not left['geographically_covered'] and right['geographically_covered'],
            'coverage_lost': left['geographically_covered'] and not right['geographically_covered'],
            'served_change': fraction(exact(right['served']) - exact(left['served'])),
            'accessibility_change': fraction(exact(right['accessibility']) - exact(left['accessibility'])),
            'nearest_seconds_change': nearest_change,
            'before_served': left['served'],
            'after_served': right['served'],
        })
    groups = defaultdict(list)
    for row in paired:
        groups[row['region']].append(row)
    regions = []
    for region, rows in sorted(groups.items()):
        regions.append({
            'region': region,
            'paired_communities': len(rows),
            'paired_demand': sum(row['demand'] for row in rows),
            'served_change': fraction(sum((exact(row['served_change']) for row in rows), Fraction(0))),
            'coverage_gained_demand': sum(row['demand'] for row in rows if row['coverage_gained']),
            'coverage_lost_demand': sum(row['demand'] for row in rows if row['coverage_lost']),
        })
    return {
        'before_digest': before['result_digest'],
        'after_digest': after['result_digest'],
        'paired': paired,
        'regions': regions,
        'unmatched': unmatched,
        'population_changed_excluded': changed_population,
        'interpretation': 'mechanical_static_scenario_comparison_not_causal_effect',
    }


def rank_changes(before, after, metric='served_change'):
    if metric not in ('served_change', 'accessibility_change'):
        raise ValueError('unknown comparison rank measure')
    report = compare(before, after)
    rows = sorted(report['paired'], key=lambda row: (exact(row[metric]), row['community']))
    return {
        'metric': metric,
        'improved': list(reversed([row for row in rows if exact(row[metric]) > 0])),
        'worsened': [row for row in rows if exact(row[metric]) < 0],
        'unchanged': [row['community'] for row in rows if exact(row[metric]) == 0],
        'excluded': report['population_changed_excluded'],
    }


def pair_transitions(before, after):
    if before['direction'] != after['direction']:
        raise ValueError('pair transitions require matching orientation')
    def pair_index(result):
        output = {}
        for row in result['catchment']['pairs']:
            output[(row['community'], row['facility'])] = {'eligible': True, 'seconds': row['seconds'], 'reason': None, 'pool': row['pool']}
        for row in result['catchment']['exclusions']:
            output[(row['community'], row['facility'])] = {'eligible': False, 'seconds': None, 'reason': row['reason'], 'pool': None}
        return output
    old = pair_index(before)
    new = pair_index(after)
    output = []
    counts = defaultdict(int)
    for key in sorted(old.keys() | new.keys()):
        first = old.get(key)
        last = new.get(key)
        if first == last:
            continue
        if first is None:
            status = 'pair_added'
        elif last is None:
            status = 'pair_removed'
        elif not first['eligible'] and last['eligible']:
            status = 'eligibility_gained'
        elif first['eligible'] and not last['eligible']:
            status = 'eligibility_lost'
        elif first['eligible']:
            status = 'route_or_pool_changed'
        else:
            status = 'exclusion_reason_changed'
        counts[status] += 1
        output.append({'community': key[0], 'facility': key[1], 'transition': status, 'before': first, 'after': last})
    return {'before_digest': before['result_digest'], 'after_digest': after['result_digest'], 'rows': output, 'counts': dict(sorted(counts.items()))}
