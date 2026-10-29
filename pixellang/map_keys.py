"""Bounded, address-independent keys for native typed maps.

The token is internal lookup data, never a persisted hash or a source-level value.
It is flat so hashing an arbitrarily nested enum does not recurse in the host.
"""
from pixellang.typeexpr import parse_type
from pixellang.spatial_values import EntityId, LinkId, SpatialValue, FrozenValue
from dataclasses import dataclass, field

MAX_KEY_ITEMS = 4096
MAX_KEY_BYTES = 1_000_000


@dataclass(frozen=True, slots=True)
class Key:
    """Lookup identity plus the language value needed for iteration and tracing.

    Heap references are intentionally excluded from equality/hash. VM graph
    operations must trace and remap value when copying or collecting a map.
    """
    identity: tuple
    value: object = field(compare=False, hash=False)


def wrap(vm, value, expected, span=None):
    return Key(token(vm, value, expected, span), value)


def supports(vm, name):
    # Derived from the VM's validated, immutable program schemas; not checkpoint state.
    if not hasattr(vm, 'map_key_types'):
        vm.map_key_types = {}
    if name in vm.map_key_types:
        return vm.map_key_types[name]
    pending, seen = [name], set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        typ = parse_type(current)
        if typ.kind == 'scalar' and typ.name in ('int', 'float64', 'bool', 'str'):
            continue
        if typ.kind == 'spatial' and typ.name in ('Point3', 'Vec3', 'EntityId', 'LinkId'):
            continue
        if current in vm.bytecode['enums']:
            for fields in vm.bytecode['enums'][current].values():
                pending.extend(fields.values())
        else:
            vm.map_key_types[name] = False
            return False
    vm.map_key_types.update((item, True) for item in seen)
    return True


def token(vm, value, expected, span=None, *, frozen=False):
    from pixellang.vm import Ref

    if not supports(vm, expected):
        vm.fail('Type does not support map keys: ' + expected, span)
    output, active = [], set()
    pending = [(iter(((value, expected),)), None)]
    size = 0
    while pending:
        iterator, owner = pending[-1]
        try:
            item, typ = next(iterator)
        except StopIteration:
            pending.pop()
            if owner is not None:
                active.remove(owner)
            continue
        if vm.cancelled:
            vm.fail('Execution cancelled', span)
        if len(output) >= MAX_KEY_ITEMS:
            vm.fail('Map key item budget exceeded', span)
        immutable_enum = frozen and isinstance(item, FrozenValue)
        if immutable_enum:
            if item.type_name != typ:
                vm.fail('Invalid frozen map key type', span)
        else:
            vm.typed(item, typ, span)
        size += len(typ.encode("utf-8")) + 32
        if type(item) in (bool, int, float, str):
            if isinstance(item, str):
                if len(item) > MAX_KEY_BYTES - size:
                    vm.fail('Map key byte budget exceeded', span)
                size += len(item.encode('utf-8'))
            if type(item) is float and item == 0:
                item = 0.0
            output.append((typ, item))
        elif type(item) in (EntityId, LinkId):
            size += len(item.owner.encode("utf-8")) + len(item.key.encode("utf-8"))
            output.append((typ, item.owner, item.key))
        elif type(item) is SpatialValue:
            output.append((typ, item.frame.unit, tuple(0.0 if x == 0 else x for x in item.xyz)))
        elif isinstance(item, Ref) or immutable_enum:
            if (not immutable_enum and vm.is_cell(item)) or typ not in vm.bytecode['enums']:
                vm.fail('Invalid map key value', span)
            identity = ('frozen', id(item)) if immutable_enum else ('heap', item.address)
            if identity in active:
                vm.fail_data('Cyclic map key', span, code='value.cycle')
            fields = dict(item.items) if immutable_enum else vm.heap[item.address]['items']
            variant = fields.get('$variant')
            variants = vm.bytecode['enums'][typ]
            if not isinstance(variant, str) or variant not in variants:
                vm.fail('Invalid enum map key', span)
            schema = variants[variant]
            if fields.keys() != schema.keys() | {'$variant'}:
                vm.fail('Invalid enum map key fields', span)
            if len(schema) > MAX_KEY_ITEMS - len(output) - 1:
                vm.fail("Map key item budget exceeded", span)
            size += len(variant.encode("utf-8"))
            output.append((typ, variant, len(schema)))
            active.add(identity)
            pending.append((_children(fields, schema), identity))
        else:
            vm.fail('Invalid map key value', span)
        if size > MAX_KEY_BYTES:
            vm.fail('Map key byte budget exceeded', span)
    return tuple(output)


def _children(fields, schema):
    for name in sorted(schema):
        yield fields[name], schema[name]
