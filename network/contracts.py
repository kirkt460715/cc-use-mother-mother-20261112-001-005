import copy
from decimal import Decimal
import hashlib
import json


class ContractError(ValueError):
    def __init__(self, path, message):
        self.path = path
        super().__init__(f'{path}: {message}')


def fields(value, required, optional=(), path='$'):
    if not isinstance(value, dict):
        raise ContractError(path, 'expected object')
    missing = set(required) - value.keys()
    extra = value.keys() - set(required) - set(optional)
    if missing or extra:
        raise ContractError(path, f'missing {sorted(missing)}, unexpected {sorted(extra)}')


def text(value, path):
    if not isinstance(value, str) or not value.strip() or len(value) > 100:
        raise ContractError(path, 'expected nonempty bounded text')
    if any(ord(char) < 32 for char in value):
        raise ContractError(path, 'control character forbidden')
    return value


def number(value, path, minimum=0, maximum=10**9):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ContractError(path, 'expected bounded integer')
    return value


def flag(value, path):
    if type(value) is not bool:
        raise ContractError(path, 'expected boolean')
    return value


def rows(value, path, maximum=100000):
    if not isinstance(value, list) or len(value) > maximum:
        raise ContractError(path, 'expected bounded list')
    return value


def identifiers(value, path):
    result = rows(value, path, 100)
    for item in result:
        text(item, path)
    if len(result) != len(set(result)):
        raise ContractError(path, 'duplicate item')
    return result


def index(values, path):
    output = {}
    for item in values:
        if item['id'] in output:
            raise ContractError(path, 'duplicate id')
        output[item['id']] = item
    return output


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def checksum(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Study:
    def __init__(self, document):
        self.document = copy.deepcopy(document)
        fields(self.document, ('version', 'nodes', 'edges', 'profiles', 'communities', 'facilities'))
        if type(document['version']) is not int or document['version'] != 1:
            raise ContractError('version', 'only version 1')
        self.nodes = self._nodes(document['nodes'])
        self.profiles = self._profiles(document['profiles'])
        self.edges = self._edges(document['edges'])
        self.communities = self._communities(document['communities'])
        self.facilities = self._facilities(document['facilities'])
        self.fingerprint = checksum(self.document)

    def _nodes(self, values):
        output = []
        for row in rows(values, 'nodes', 20000):
            fields(row, ('id', 'region', 'rural'))
            text(row['id'], 'node.id')
            text(row['region'], 'node.region')
            flag(row['rural'], 'node.rural')
            output.append(copy.deepcopy(row))
        return index(output, 'nodes')

    def _profiles(self, values):
        output = []
        for row in rows(values, 'profiles', 20):
            fields(row, ('id', 'seconds_per_km', 'step_free', 'max_slope_permille', 'max_seconds', 'decay'))
            text(row['id'], 'profile.id')
            number(row['seconds_per_km'], 'profile.seconds_per_km', 1, 100000)
            flag(row['step_free'], 'profile.step_free')
            number(row['max_slope_permille'], 'profile.max_slope_permille', 0, 1000)
            number(row['max_seconds'], 'profile.max_seconds', 1, 86400)
            decay = rows(row['decay'], 'profile.decay', 100)
            previous_time = -1
            previous_weight = 1000001
            for point in decay:
                fields(point, ('seconds', 'weight_ppm'))
                number(point['seconds'], 'decay.seconds', 0, row['max_seconds'])
                number(point['weight_ppm'], 'decay.weight_ppm', 1, 1000000)
                if point['seconds'] <= previous_time or point['weight_ppm'] > previous_weight:
                    raise ContractError('decay', 'times must increase and weights must not increase')
                previous_time = point['seconds']
                previous_weight = point['weight_ppm']
            if not decay or decay[-1]['seconds'] != row['max_seconds']:
                raise ContractError('decay', 'last upper bound must equal maximum travel time')
            output.append(copy.deepcopy(row))
        return index(output, 'profiles')

    def _edges(self, values):
        output = []
        for row in rows(values, 'edges', 100000):
            fields(row, ('id', 'from', 'to', 'metres', 'delay_seconds', 'steps', 'slope_permille', 'closed', 'profiles'))
            text(row['id'], 'edge.id')
            if row['from'] not in self.nodes or row['to'] not in self.nodes:
                raise ContractError('edges', 'unknown endpoint')
            if row['from'] == row['to']:
                raise ContractError('edges', 'self loops not supported')
            number(row['metres'], 'edge.metres', 1, 1000000)
            number(row['delay_seconds'], 'edge.delay_seconds', 0, 86400)
            number(row['steps'], 'edge.steps', 0, 10000)
            number(row['slope_permille'], 'edge.slope_permille', 0, 1000)
            flag(row['closed'], 'edge.closed')
            identifiers(row['profiles'], 'edge.profiles')
            if set(row['profiles']) - self.profiles.keys():
                raise ContractError('edges', 'unknown allowed profile')
            output.append(copy.deepcopy(row))
        return index(output, 'edges')

    def _communities(self, values):
        output = []
        for row in rows(values, 'communities', 10000):
            fields(row, ('id', 'node', 'profile', 'service', 'demand', 'priority', 'max_fee_cents'))
            text(row['id'], 'community.id')
            text(row['service'], 'community.service')
            if row['node'] not in self.nodes or row['profile'] not in self.profiles:
                raise ContractError('communities', 'unknown node or profile')
            number(row['demand'], 'community.demand', 0, 10000000)
            number(row['priority'], 'community.priority', 0, 1000)
            number(row['max_fee_cents'], 'community.max_fee_cents', 0, 10000000)
            output.append(copy.deepcopy(row))
        return index(output, 'communities')

    def _facilities(self, values):
        output = []
        for row in rows(values, 'facilities', 10000):
            fields(row, ('id', 'node', 'service', 'capacity', 'fee_cents', 'profiles', 'open', 'pool'))
            text(row['id'], 'facility.id')
            text(row['service'], 'facility.service')
            text(row['pool'], 'facility.pool')
            if row['node'] not in self.nodes:
                raise ContractError('facilities', 'unknown node')
            number(row['capacity'], 'facility.capacity', 0, 10000000)
            number(row['fee_cents'], 'facility.fee_cents', 0, 10000000)
            identifiers(row['profiles'], 'facility.profiles')
            if set(row['profiles']) - self.profiles.keys():
                raise ContractError('facilities', 'unknown profile')
            flag(row['open'], 'facility.open')
            output.append(copy.deepcopy(row))
        result = index(output, 'facilities')
        pools = {}
        for row in result.values():
            key = row['pool']
            if key in pools and pools[key] != row['capacity']:
                raise ContractError('facilities.pool', 'shared pool members must declare same pool capacity')
            pools[key] = row['capacity']
        return result

    def export(self):
        return copy.deepcopy(self.document)
