from collections import defaultdict
from fractions import Fraction
from network.routing import Graph, decay_weight
from network.contracts import checksum


def fraction(value):
    value = Fraction(value)
    return {'numerator': value.numerator, 'denominator': value.denominator}


def exact(value):
    if not isinstance(value, dict) or set(value) != {'numerator', 'denominator'}:
        raise ValueError('invalid rational value')
    if type(value['numerator']) is not int or type(value['denominator']) is not int or value['denominator'] <= 0:
        raise ValueError('invalid rational components')
    return Fraction(value['numerator'], value['denominator'])


def eligibility(community, facility):
    if not facility['open']:
        return 'facility_closed'
    if facility['service'] != community['service']:
        return 'service_mismatch'
    if community['profile'] not in facility['profiles']:
        return 'facility_profile_ineligible'
    if facility['fee_cents'] > community['max_fee_cents']:
        return 'fee_above_limit'
    return None


def build(study, direction='outbound'):
    if direction not in ('outbound', 'return', 'roundtrip'):
        raise ValueError('unknown journey direction')
    graphs = {ident: Graph(study, ident) for ident in study.profiles}
    reversed_graphs = {ident: Graph(study, ident, reverse=True) for ident in study.profiles}
    trees = {}
    pairs = []
    exclusions = []
    for community_id, community in sorted(study.communities.items()):
        profile = study.profiles[community['profile']]
        key = community['profile'], community['node']
        if key not in trees:
            forward = graphs[key[0]].shortest(key[1])
            reverse = reversed_graphs[key[0]].shortest(key[1])
            trees[key] = forward, reverse
        forward, reverse = trees[key]
        for facility_id, facility in sorted(study.facilities.items()):
            reason = eligibility(community, facility)
            outward = forward['distances'].get(facility['node'])
            returning = reverse['distances'].get(facility['node'])
            travel = outward if direction == 'outbound' else returning
            if direction == 'roundtrip':
                travel = outward + returning if outward is not None and returning is not None else None
            if reason is None and travel is None:
                reason = 'no_directed_route'
            if reason is None and travel > profile['max_seconds']:
                reason = 'travel_time_exceeded'
            if reason is not None:
                exclusions.append({'community': community_id, 'facility': facility_id, 'reason': reason, 'outbound_seconds': outward, 'return_seconds': returning})
                continue
            weight = decay_weight(profile, travel)
            pairs.append({
                'community': community_id,
                'facility': facility_id,
                'pool': facility['pool'],
                'profile': profile['id'],
                'service': community['service'],
                'demand': community['demand'],
                'seconds': travel,
                'outbound_seconds': outward,
                'return_seconds': returning,
                'decay': fraction(weight),
                'outbound_route': graphs[profile['id']].route(forward, facility['node']) if outward is not None else None,
                'return_route_reversed': reversed_graphs[profile['id']].route(reverse, facility['node']) if returning is not None else None,
            })
    body = {'input_digest': study.fingerprint, 'direction': direction, 'pairs': pairs, 'exclusions': exclusions, 'unique_origin_profile_searches': len(trees)}
    return {**body, 'catchment_digest': checksum(body)}


def verify(study, catchment, replay=False):
    body = {key: value for key, value in catchment.items() if key != 'catchment_digest'}
    if checksum(body) != catchment.get('catchment_digest') or catchment['input_digest'] != study.fingerprint:
        raise ValueError('catchment digest mismatch')
    pairs = {(row['community'], row['facility']) for row in catchment['pairs']}
    exclusions = {(row['community'], row['facility']) for row in catchment['exclusions']}
    all_pairs = {(community, facility) for community in study.communities for facility in study.facilities}
    if pairs & exclusions or pairs | exclusions != all_pairs:
        raise ValueError('catchment pair partition mismatch')
    if len(pairs) != len(catchment['pairs']) or len(exclusions) != len(catchment['exclusions']):
        raise ValueError('duplicate catchment pair')
    if replay and build(study, catchment['direction']) != catchment:
        raise ValueError('catchment replay mismatch')
    return True
