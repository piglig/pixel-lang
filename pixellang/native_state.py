"""Closed data-only encoding for immutable native application values.

No Python class name, callable, heap reference or instruction pointer is loaded
from the document. Nominal schemas and coordinate frames come from the program.
"""
from dataclasses import fields
import math
import json

from . import jsonvalue, map_keys, spatial_values as spatial
from .model import PixelError
from .typeexpr import parse_type, map_parts

KINDS = {cls.__name__: cls for cls in spatial._NATIVE_VALUE_TYPES}


def invalid(message):
    raise PixelError('event', 'Invalid native state: ' + message)


def integer(value, minimum=0):
    if type(value) is not int or not minimum <= value < (1 << 63):
        invalid('integer outside its range')
    return value


class NativeStateLimit(PixelError):
    pass


class NativeStateCodec:
    def __init__(self, vm, max_nodes=4_000_000, max_bytes=64_000_000):
        self.vm = vm
        self.encode_nodes = self.decode_nodes = max_nodes
        self.encode_bytes = max_bytes
        self.memo = {}
        self.decoded = []
        self.checked = set()

    def encode(self, value):
        def visit(item, depth=0):
            self.encode_nodes -= 1
            self.encode_bytes -= 64
            if type(item) is str:
                self.encode_bytes -= len(json.dumps(item, ensure_ascii=False).encode())
            if self.encode_nodes < 0 or self.encode_bytes < 0 or depth > 512:
                raise NativeStateLimit('event', 'Native checkpoint encoding budget exceeded')
            if item is None or type(item) in (str, bool): return item
            if type(item) is int: return integer(item, -(1 << 63))
            if type(item) is float:
                if not math.isfinite(item): invalid('non-finite number')
                return {'float': item}
            if type(item) not in (*KINDS.values(), tuple, jsonvalue.JsonValue):
                invalid('unsupported value')
            prior = self.memo.get(id(item))
            if prior is not None: return {'ref': prior[1]}
            identity = len(self.memo)
            self.memo[id(item)] = (item, identity)
            if type(item) is tuple:
                return {'id': identity, 'tuple': [visit(v, depth + 1) for v in item]}
            if type(item) is jsonvalue.JsonValue:
                text = jsonvalue.stringify(item)
                self.encode_bytes -= len(json.dumps(text, ensure_ascii=False).encode())
                if self.encode_bytes < 0: raise NativeStateLimit('event', 'Native checkpoint encoding budget exceeded')
                return {'id': identity, 'json': text}
            return {'id': identity, 'native': type(item).__name__, 'fields':
                    {f.name: visit(getattr(item, f.name), depth + 1) for f in fields(item)}}
        try:
            return visit(value)
        except RecursionError:
            raise NativeStateLimit('event', 'Native checkpoint nesting budget exceeded') from None

    def decode(self, document):
        def visit(item, depth=0):
            self.decode_nodes -= 1
            if self.decode_nodes < 0 or depth > 512: invalid('value structure budget exceeded')
            if item is None or type(item) in (str, bool): return item
            if type(item) is int: return integer(item, -(1 << 63))
            if not isinstance(item, dict): invalid('expected a tagged value')
            if set(item) == {'float'}:
                value = item['float']
                if type(value) is not float or not math.isfinite(value): invalid('invalid float64')
                return value
            if set(item) == {'ref'}:
                identity = integer(item['ref'])
                if identity >= len(self.decoded) or self.decoded[identity] is None:
                    invalid('unknown or cyclic native value reference')
                return self.decoded[identity]
            identity = integer(item.get('id'))
            if identity != len(self.decoded): invalid('native value identities must be sequential')
            self.decoded.append(None)
            if set(item) == {'id', 'tuple'} and isinstance(item['tuple'], list):
                result = tuple(visit(v, depth + 1) for v in item['tuple'])
            elif set(item) == {'id', 'json'} and type(item['json']) is str:
                result = jsonvalue.parse(item['json'])
            else:
                if (set(item) != {'id', 'native', 'fields'} or type(item['native']) is not str
                        or item['native'] not in KINDS or not isinstance(item['fields'], dict)):
                    invalid('unknown value tag')
                cls = KINDS[item['native']]
                if set(item['fields']) != {f.name for f in fields(cls)}: invalid('native fields do not match')
                # JSON object ordering is not semantic; follow the codec's fixed
                # field order so references never depend on key serialization.
                values = {f.name: visit(item['fields'][f.name], depth + 1) for f in fields(cls)}
                try:
                    result = cls(**values)
                    self._native(result)
                except (ValueError, TypeError, AttributeError, KeyError) as error:
                    invalid(str(error))
            self.decoded[identity] = result
            return result
        try:
            return visit(document)
        except RecursionError:
            invalid('value nesting budget exceeded')

    def typed(self, value, expected):
        if type(expected) is not str: invalid('type name must be a string')
        if isinstance(value, spatial.FrozenValue):
            if type(value.items) is not tuple: invalid('frozen container storage must be a tuple')
            key = (id(value), expected)
            if key in self.checked: return
            self._typed(value, expected)
            self.checked.add(key)
        else:
            self._typed(value, expected)

    def _typed(self, value, expected):
        if isinstance(value, spatial.FrozenValue):
            if value.type_name != expected: invalid('frozen nominal type does not match')
            typ = parse_type(expected)
            if typ.kind == 'array':
                for item in value.items: self.typed(item, typ.args[0].spelling())
                return
            if typ.kind == 'map':
                key_type, value_type = map_parts(expected)
                if not map_keys.supports(self.vm, key_type): invalid('unsupported map key type')
                seen = set()
                for pair in value.items:
                    if type(pair) is not tuple or len(pair) != 2: invalid('invalid map entry')
                    key, item = pair
                    self.typed(key, key_type)
                    identity = map_keys.token(self.vm, key, key_type, frozen=True)
                    if identity in seen: invalid('repeated map key')
                    seen.add(identity)
                    self.typed(item, value_type)
                return
            items = self._pairs(value.items)
            if expected in self.vm.bytecode['enums']:
                variant = items.get('$variant')
                if type(variant) is not str or variant not in self.vm.bytecode['enums'][expected]:
                    invalid('unknown enum variant')
                schema = {'$variant': 'str', **self.vm.bytecode['enums'][expected][variant]}
            else:
                schema = self.vm.bytecode['records'].get(expected)
            if schema is None or set(items) != set(schema): invalid('record fields do not match')
            for name, typ in schema.items(): self.typed(items[name], typ)
            return
        if expected == 'unit' and type(value) is int and value == 0: return
        self.vm.typed(value, expected, None)
        if type(value) is int: integer(value, -(1 << 63))
        if type(value) is float and not math.isfinite(value): invalid('non-finite number')

    @staticmethod
    def _pairs(items):
        result = {}
        for pair in items:
            if type(pair) is not tuple or len(pair) != 2 or type(pair[0]) is not str or pair[0] in result:
                invalid('invalid or repeated record/map field')
            result[pair[0]] = pair[1]
        return result

    def _native(self, value):
        if isinstance(value, spatial.CoordinateFrame):
            if self.vm.bytecode['coordinateFrames'].get(value.identity) != value.unit:
                invalid('coordinate frame does not belong to the program')
        elif isinstance(value, spatial.SpatialOrigin):
            if type(value.function) is not str or type(value.node) is not int:
                invalid('invalid spatial source origin')
        elif isinstance(value, spatial.SpatialValue):
            if value.origin is not None and not isinstance(value.origin, spatial.SpatialOrigin):
                invalid('invalid spatial source origin')
        elif isinstance(value, spatial.FrozenValue):
            self.typed(value, value.type_name)
        elif isinstance(value, (spatial.EntityId, spatial.LinkId)):
            if type(value.owner) is not str or value.owner not in self.vm.space_identities:
                invalid('unknown spatial identity owner')
            spatial.business_key(value.key)
            if not isinstance(value.frame, spatial.CoordinateFrame): invalid('invalid identity frame')
            parse_type(value.payload_type)
            if isinstance(value, spatial.LinkId): parse_type(value.entity_type)
        elif isinstance(value, spatial.EntityValue):
            if type(value.owner) is not str or value.owner not in self.vm.space_identities:
                invalid('unknown entity space')
            spatial.business_key(value.identity)
            if not isinstance(value.position, spatial.SpatialValue) or value.position.kind != 'Point3':
                invalid('entity position is not a point')
            self.typed(value.payload, value.payload_type)
        elif isinstance(value, spatial.LinkValue):
            if type(value.owner) is not str or value.owner not in self.vm.space_identities:
                invalid('unknown link space')
            for key in (value.identity, value.source, value.target): spatial.business_key(key)
            if not isinstance(value.frame, spatial.CoordinateFrame): invalid('invalid link frame')
            parse_type(value.entity_type)
            self.typed(value.payload, value.payload_type)
        elif isinstance(value, spatial.SpaceValue):
            if type(value.identity) is not str or value.identity not in self.vm.space_identities:
                invalid('unknown space identity')
            if not isinstance(value.frame, spatial.CoordinateFrame): invalid('invalid space frame')
            integer(value.version)
            if type(value.entities) is not tuple or type(value.links) is not tuple: invalid('invalid space storage')
            ids = set()
            for entity in value.entities:
                if (not isinstance(entity, spatial.EntityValue) or entity.owner != value.identity
                        or entity.position.frame != value.frame or entity.payload_type != value.payload_type
                        or entity.identity in ids):
                    invalid('invalid or repeated entity identity')
                spatial.business_key(entity.identity)
                ids.add(entity.identity)
            link_ids = set()
            for link in value.links:
                if (not isinstance(link, spatial.LinkValue) or link.owner != value.identity
                        or link.frame != value.frame or link.entity_type != value.payload_type
                        or link.payload_type != value.link_type or link.source not in ids or link.target not in ids
                        or link.identity in link_ids):
                    invalid('invalid link or dangling endpoint')
                spatial.business_key(link.identity)
                link_ids.add(link.identity)


def cell_types(vm):
    instance = vm.flow_runtime.instances[vm.event_binding.handle.identity]
    result = {}
    for path, cell in instance.cells:
        graph = vm.bytecode['flows'][instance.handle.target]
        for node in path:
            target = next(item['function'] for item in graph['children'] if item['node'] == node)
            graph = vm.bytecode['flows'][target]
        result[(path, cell)] = next(item['type'] for item in graph['states'] if item['node'] == cell)
    return result
