from collections import defaultdict
from fractions import Fraction
from catchments.pairs import exact, fraction
from network.contracts import checksum, number


DIMENSIONS = {'region', 'rural', 'profile', 'service', 'priority'}


def observations(study, catchment, index_report, allocation):
    if any(report['input_digest'] != study.fingerprint for report in (catchment, index_report, allocation)):
        raise ValueError('equity source mismatch')
    if index_report['catchment_digest'] != catchment['catchment_digest'] or allocation['catchment_digest'] != catchment['catchment_digest']:
        raise ValueError('equity catchment mismatch')
    scores = {row['community']: exact(row['accessibility']) for row in index_report['communities']}
    served = {row['community']: exact(row['served']) for row in allocation['communities']}
    pairs = defaultdict(list)
    exclusions = defaultdict(list)
    for row in catchment['pairs']:
        pairs[row['community']].append(row)
    for row in catchment['exclusions']:
        exclusions[row['community']].append(row)
    output = []
    for ident, community in sorted(study.communities.items()):
        node = study.nodes[community['node']]
        options = pairs[ident]
        output.append({
            'community': ident,
            'region': node['region'],
            'rural': node['rural'],
            'profile': community['profile'],
            'service': community['service'],
            'priority': community['priority'],
            'demand': community['demand'],
            'geographically_covered': bool(options),
            'nearest_seconds': min((row['seconds'] for row in options), default=None),
            'eligible_pools': len({row['pool'] for row in options}),
            'accessibility': fraction(scores[ident]),
            'served': fraction(served[ident]),
            'unserved': fraction(Fraction(community['demand']) - served[ident]),
            'served_share': fraction(served[ident] / community['demand']) if community['demand'] else None,
            'no_capacity_after_competition': bool(options) and bool(community['demand']) and served[ident] == 0,
            'excluded_facilities': len(exclusions[ident]),
        })
    return output


def weighted_quantile(values, probability):
    probability = Fraction(probability)
    if not 0 <= probability <= 1:
        raise ValueError('probability outside zero to one')
    selected = []
    for value, weight in values:
        number(weight, 'weight', 0, 10**12)
        if weight:
            selected.append((Fraction(value), weight))
    selected.sort()
    total = sum(weight for value, weight in selected)
    if not total:
        return None
    cumulative = 0
    for value, weight in selected:
        cumulative += weight
        if cumulative >= probability * total:
            return value
    return selected[-1][0]


def weighted_gini(values):
    selected = [(Fraction(value), weight) for value, weight in values if weight]
    if any(value < 0 or type(weight) is not int or weight < 0 for value, weight in selected):
        raise ValueError('Gini requires nonnegative values and weights')
    selected.sort()
    total_weight = sum(weight for value, weight in selected)
    total_value = sum((value * weight for value, weight in selected), Fraction(0))
    if not total_weight or not total_value:
        return None
    cumulative_weight = 0
    cumulative_value = Fraction(0)
    differences = Fraction(0)
    for value, weight in selected:
        differences += weight * (value * cumulative_weight - cumulative_value)
        cumulative_weight += weight
        cumulative_value += value * weight
    return differences / (total_weight * total_value)


def aggregate(rows, dimensions):
    if not isinstance(dimensions, list) or len(dimensions) != len(set(dimensions)) or set(dimensions) - DIMENSIONS:
        raise ValueError('unsupported equity dimensions')
    groups = defaultdict(list)
    for row in rows:
        groups[tuple(row[key] for key in dimensions)].append(row)
    output = []
    for key, values in sorted(groups.items(), key=lambda pair: tuple(str(value) for value in pair[0])):
        demand = sum(row['demand'] for row in values)
        served = sum((exact(row['served']) for row in values), Fraction(0))
        geographic = sum(row['demand'] for row in values if row['geographically_covered'])
        weighted_index = sum((row['demand'] * exact(row['accessibility']) for row in values), Fraction(0))
        reachable = [(row['nearest_seconds'], row['demand']) for row in values if row['nearest_seconds'] is not None]
        indices = [(exact(row['accessibility']), row['demand']) for row in values]
        quantiles = {name: weighted_quantile(reachable, probability) for name, probability in [('p50', Fraction(1, 2)), ('p90', Fraction(9, 10))]}
        inequality = weighted_gini(indices)
        output.append({
            **dict(zip(dimensions, key)),
            'communities': len(values),
            'demand': demand,
            'geographic_covered_demand': geographic,
            'geographic_coverage_share': fraction(Fraction(geographic, demand)) if demand else None,
            'served': fraction(served),
            'unserved': fraction(Fraction(demand) - served),
            'served_share': fraction(served / demand) if demand else None,
            'mean_accessibility': fraction(weighted_index / demand) if demand else None,
            'accessibility_gini': fraction(inequality) if inequality is not None else None,
            'nearest_travel_p50': fraction(quantiles['p50']) if quantiles['p50'] is not None else None,
            'nearest_travel_p90': fraction(quantiles['p90']) if quantiles['p90'] is not None else None,
            'travel_quantiles_exclude_unreachable_demand': True,
            'zero_demand_communities': sum(row['demand'] == 0 for row in values),
        })
    return {'dimensions': dimensions, 'rows': output}


def threshold_coverage(rows, thresholds):
    if not isinstance(thresholds, list) or not thresholds or len(thresholds) != len(set(thresholds)):
        raise ValueError('thresholds require nonempty unique list')
    for threshold in thresholds:
        number(threshold, 'threshold', 0, 86400)
    total = sum(row['demand'] for row in rows)
    output = []
    for threshold in sorted(thresholds):
        covered = sum(row['demand'] for row in rows if row['nearest_seconds'] is not None and row['nearest_seconds'] <= threshold)
        output.append({'seconds': threshold, 'covered_demand': covered, 'total_demand': total, 'coverage_share': fraction(Fraction(covered, total)) if total else None})
    return output


def rural_urban_gap(rows):
    report = aggregate(rows, ['rural'])
    groups = {row['rural']: row for row in report['rows']}
    rural, urban = groups.get(True), groups.get(False)
    output = {'rural': rural, 'urban': urban, 'served_share_gap_rural_minus_urban': None, 'accessibility_gap_rural_minus_urban': None}
    if rural and urban:
        if rural['served_share'] is not None and urban['served_share'] is not None:
            output['served_share_gap_rural_minus_urban'] = fraction(exact(rural['served_share']) - exact(urban['served_share']))
        if rural['mean_accessibility'] is not None and urban['mean_accessibility'] is not None:
            output['accessibility_gap_rural_minus_urban'] = fraction(exact(rural['mean_accessibility']) - exact(urban['mean_accessibility']))
    return output
