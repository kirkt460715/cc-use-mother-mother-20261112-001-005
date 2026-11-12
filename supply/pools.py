from collections import defaultdict
from fractions import Fraction
from network.contracts import checksum
from catchments.pairs import exact, fraction, verify


def capacities(study):
    values = {}
    for facility in study.facilities.values():
        values[facility['pool']] = facility['capacity']
    return values


def connections(study, catchment):
    verify(study, catchment)
    candidates = defaultdict(list)
    for pair in catchment['pairs']:
        candidates[(pair['community'], pair['pool'])].append(pair)
    rows = []
    for (community, pool), options in sorted(candidates.items()):
        selected = min(options, key=lambda row: (-exact(row['decay']), row['seconds'], row['facility']))
        rows.append({
            'community': community,
            'pool': pool,
            'facility': selected['facility'],
            'seconds': selected['seconds'],
            'decay': selected['decay'],
            'eligible_facilities_in_pool': sorted(row['facility'] for row in options),
            'pool_capacity': capacities(study)[pool],
            'duplicate_pool_capacity_counted_once': True,
        })
    return rows


def floating_catchment(study, catchment):
    links = connections(study, catchment)
    pools = capacities(study)
    by_pool = defaultdict(list)
    by_community = defaultdict(list)
    for link in links:
        by_pool[link['pool']].append(link)
        by_community[link['community']].append(link)
    ratios = {}
    pool_rows = []
    for pool, capacity in sorted(pools.items()):
        denominator = sum((Fraction(study.communities[link['community']]['demand']) * exact(link['decay']) for link in by_pool[pool]), Fraction(0))
        ratio = Fraction(capacity) / denominator if denominator else Fraction(0)
        ratios[pool] = ratio
        pool_rows.append({
            'pool': pool,
            'capacity': capacity,
            'weighted_competing_demand': fraction(denominator),
            'supply_demand_ratio': fraction(ratio),
            'eligible_communities': len(by_pool[pool]),
            'no_competing_demand': not denominator,
        })
    community_rows = []
    for ident, community in sorted(study.communities.items()):
        terms = []
        for link in by_community[ident]:
            score = ratios[link['pool']] * exact(link['decay'])
            terms.append({'pool': link['pool'], 'facility': link['facility'], 'contribution': fraction(score)})
        score = sum((exact(term['contribution']) for term in terms), Fraction(0))
        community_rows.append({
            'community': ident,
            'demand': community['demand'],
            'accessibility': fraction(score),
            'terms': terms,
            'eligible_pools': len(terms),
        })
    body = {
        'input_digest': study.fingerprint,
        'catchment_digest': catchment['catchment_digest'],
        'pools': pool_rows,
        'communities': community_rows,
        'interpretation': 'two_step_floating_catchment_index_not_an_actual_service_assignment',
        'shared_pool_rule': 'one_best_decay_connection_per_community_pool',
    }
    return {**body, 'index_digest': checksum(body)}


def audit_index(study, catchment, report):
    if floating_catchment(study, catchment) != report:
        raise ValueError('floating catchment index replay mismatch')
    scores = {row['community']: exact(row['accessibility']) for row in report['communities']}
    weighted = sum((study.communities[ident]['demand'] * score for ident, score in scores.items()), Fraction(0))
    active_capacity = sum(row['capacity'] for row in report['pools'] if exact(row['weighted_competing_demand']))
    if weighted != active_capacity:
        raise ValueError('floating catchment weighted index fails capacity identity')
    return {'valid': True, 'demand_weighted_accessibility': fraction(weighted), 'capacity_with_competing_demand': active_capacity}
