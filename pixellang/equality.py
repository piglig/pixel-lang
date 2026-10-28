"""Bounded structural equality for statically comparable PixelLang values."""
from .value_map import ValueMap


from .typeexpr import parse_type
from .spatial_values import SpatialValue

MAX_COMPARISON_ITEMS = 1_000_000
SCALARS = (int, float, bool, str)


def mapping_pairs(first, second):
    for key, value in first.items():
        yield value, second[key]


def supports(vm, name):
    """Inspect all possible fields, not just the active enum variant or contents."""
    if name in vm.equality_types:
        return vm.equality_types[name]
    pending, seen = [name], set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        typ = parse_type(current)
        if typ.kind == 'scalar' and typ.name in ('int', 'float64', 'bool', 'str'):
            continue
        if typ.kind == 'spatial' and typ.name in ('Point3', 'Vec3'):
            continue
        if typ.kind in ('array', 'map'):
            pending.extend(arg.spelling() for arg in typ.args)
        elif current in vm.bytecode['records']:
            pending.extend(vm.bytecode['records'][current].values())
        elif current in vm.bytecode['enums']:
            for fields in vm.bytecode['enums'][current].values():
                pending.extend(fields.values())
        else:
            vm.equality_types[name] = False
            return False
    vm.equality_types.update((item, True) for item in seen)
    return True


def equal(vm, left, right, span=None):
    """Compare values, ignoring allocation identities and internal sharing.

    Repeated reference pairs terminate cyclic comparisons (bisimulation). Lazy
    iterators avoid eagerly copying container contents. The independent work cap
    keeps one equality instruction bounded without changing instruction indices
    used by time-travel debugging. No heap values are allocated or mutated.
    """
    from .vm import Ref

    name = vm.value_type(left)
    if name != vm.value_type(right):
        vm.fail('Equality operands have different types', span)
    if not supports(vm, name):
        vm.fail('Type does not support value equality: ' + name, span)
    pending = [iter(((left, right),))]
    seen = set()
    work = 0
    while pending:
        try:
            a, b = next(pending[-1])
        except StopIteration:
            pending.pop()
            continue
        work += 1
        if work > MAX_COMPARISON_ITEMS:
            vm.fail('Equality comparison budget exceeded', span)
        if vm.cancelled:
            vm.fail('Execution cancelled', span)
        if type(a) is not type(b):
            vm.fail('Equality operands have different types', span)
        if type(a) in SCALARS:
            if a != b:
                return False
        elif type(a) is SpatialValue:
            if vm.value_type(a) != vm.value_type(b):
                vm.fail('Equality operands have different types', span)
            if a != b:
                return False
        elif isinstance(a, Ref):
            if vm.is_cell(a) or vm.is_cell(b):
                vm.fail('Binding cells are not comparable values', span)
            if vm.value_type(a) != vm.value_type(b):
                vm.fail('Equality operands have different types', span)
            pair = (a.address, b.address)
            if a.address == b.address or pair in seen:
                continue
            seen.add(pair)
            first, second = vm.heap[a.address]['items'], vm.heap[b.address]['items']
            if len(first) != len(second):
                return False
            if isinstance(first, (dict, ValueMap)):
                work += len(first)
                if work > MAX_COMPARISON_ITEMS:
                    vm.fail('Equality comparison budget exceeded', span)
                if first.keys() != second.keys():
                    return False
                # Bind the dictionaries into this iterator: a generator closing
                # over loop locals would observe a later nested container.
                pending.append(mapping_pairs(first, second))
            else:
                pending.append(iter(zip(first, second)))
        else:
            vm.fail('Invalid equality value', span)
    return True
