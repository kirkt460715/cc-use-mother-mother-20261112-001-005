from network.contracts import number, checksum
from equity.metrics import aggregate


def protected(rows, dimensions, minimum_communities=3, minimum_demand=10):
    number(minimum_communities, 'minimum_communities', 1, 100000)
    number(minimum_demand, 'minimum_demand', 1, 100000000)
    report = aggregate(rows, dimensions)
    hidden = [index for index, row in enumerate(report['rows']) if row['communities'] < minimum_communities or row['demand'] < minimum_demand]
    if len(hidden) == 1 and len(report['rows']) > 1:
        alternatives = [index for index in range(len(report['rows'])) if index not in hidden]
        hidden.append(min(alternatives, key=lambda index: (report['rows'][index]['communities'], report['rows'][index]['demand'], index)))
    output = []
    for index, row in enumerate(report['rows']):
        if index in hidden:
            output.append({**{key: row[key] for key in dimensions}, 'suppressed': True})
        else:
            output.append({**row, 'suppressed': False})
    return {'dimensions': dimensions, 'rows': output, 'total_publishable': not hidden}


def public_packet(rows, result_digest, minimum_communities=3, minimum_demand=10):
    reports = [protected(rows, dimensions, minimum_communities, minimum_demand) for dimensions in ([], ['region'], ['rural'], ['profile'], ['service'])]
    if any(not report['total_publishable'] for report in reports):
        reports[0]['rows'] = [{'suppressed': True}]
        reports[0]['total_publishable'] = False
    body = {
        'result_digest': result_digest,
        'minimum_communities': minimum_communities,
        'minimum_demand': minimum_demand,
        'reports': reports,
        'limitations': 'partition_suppression_only_not_arbitrary_cross_release_privacy',
        'contains_routes_or_community_ids': False,
    }
    return {**body, 'public_digest': checksum(body)}


def verify(packet):
    if checksum({key: value for key, value in packet.items() if key != 'public_digest'}) != packet.get('public_digest'):
        raise ValueError('public packet checksum mismatch')
    if packet['contains_routes_or_community_ids']:
        raise ValueError('public packet contains restricted identifiers')
    for report in packet['reports']:
        for row in report['rows']:
            if row['suppressed'] and set(row) - set(report['dimensions']) - {'suppressed'}:
                raise ValueError('suppressed row retains measures')
        if report['total_publishable'] and any(row['suppressed'] for row in report['rows']):
            raise ValueError('publishable total with hidden groups')
    return True
