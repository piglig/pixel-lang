"""Persistent writes to ordinary values; never mutate an aliased heap object."""
from .value_map import ValueMap

from . import map_keys
from .value_vector import ValueVector
from .typeexpr import array_item, is_typed_map, map_parts


def check_path(vm, base, path, indices, span):
    from .vm import Ref

    parents = []
    current = base
    position = 0
    for depth, selector in enumerate(path):
        if vm.cancelled:
            vm.fail('Execution cancelled', span)
        if not isinstance(current, Ref):
            vm.fail('Value update path requires a record, array or map', span)
        obj = vm.heap[current.address]
        if not obj.get('immutable') or obj.get('cell'):
            vm.fail('Value update requires value storage', span)
        typ, items = obj['type'], obj['items']
        if 'field' in selector:
            key = selector['field']
            schema = vm.bytecode['records'].get(typ)
            if schema is None or key not in schema:
                vm.fail('Unknown value update field', span)
        else:
            key = indices[position]
            position += 1
            if is_typed_map(typ):
                key_type, _ = map_parts(typ)
                key = map_keys.wrap(vm, key, key_type, span)
                if key not in items and depth + 1 < len(path):
                    vm.fail('Value update map key does not exist', span)
            elif array_item(typ):
                vm.typed(key, 'int', span)
                if not 0 <= key < len(items):
                    vm.fail('Value update index out of bounds', span)
            else:
                vm.fail('Value update index requires an array or map', span)
        parents.append((typ, items, key))
        if depth + 1 < len(path):
            current = items[key]
    return parents


def _checked_child(vm, value, expected, span):
    from .vm import Ref
    from .typeexpr import parse_type

    vm.typed(value, expected, span)
    if (isinstance(value, Ref) and not vm.is_cell(value)
            and parse_type(vm.value_type(value)).kind != 'function'
            and not vm.heap[value.address].get('immutable', False)):
        vm.fail('Readonly allocation requires immutable children', span)


def _publish_copy(vm, typ, items, span):
    """Publish an owned copy of validated value storage.

    Private to this module: inherited entries came from an immutable VM object;
    callers check changed values and normalize new keys before reaching here.
    This is not a general allocation entry point for bytecode or decoded input.
    """
    from .vm import Ref, LEAF_CONTAINERS

    if vm.cancelled:
        vm.fail('Execution cancelled', span)
    charge = vm.vector_budget.additional(items) if isinstance(items, ValueVector) else len(items)
    if (vm.allocations_since_gc >= max(128, len(vm.heap) // 2)
            or vm.heap_items + charge > vm.max_heap_items
            or len(vm.heap) >= vm.max_heap_objects):
        roots = ()
        if typ not in LEAF_CONTAINERS:
            roots = list(items.values()) if isinstance(items, (dict, ValueMap)) else items
            if is_typed_map(typ):
                roots.extend(key.value for key in items)
        vm.collect(roots)
        charge = vm.vector_budget.additional(items) if isinstance(items, ValueVector) else len(items)
    if vm.heap_items + charge > vm.max_heap_items or len(vm.heap) >= vm.max_heap_objects:
        vm.fail('Heap budget exceeded', span)
    if not isinstance(items, ValueVector) and array_item(typ) == 'int' and len(items) >= 4096:
        try:
            items = bytearray(items)
        except (ValueError, OverflowError):
            pass
    address = vm.next_address
    vm.next_address += 1
    vm.allocations_since_gc += 1
    vm.heap[address] = {'type': typ, 'items': items, 'immutable': True}
    vm.heap_items += (vm.vector_budget.retain(items)
                      if isinstance(items, ValueVector) else len(items))
    return Ref(address)


def replace_path(vm, base, path, indices, replacement, span):
    """Copy only the ancestors of one write and share all untouched subtrees.

    The caller has already evaluated the base, dynamic indices and replacement.
    An invalid path or exhausted allocation budget leaves every old object intact.
    """
    root_mark = len(vm.stack)
    vm.stack.extend([base, replacement, *indices])
    try:
        parents = check_path(vm, base, path, indices, span)
        value = replacement
        for typ, items, key in reversed(parents):
            if is_typed_map(typ):
                expected = map_parts(typ)[1]
            elif array_item(typ):
                expected = array_item(typ)
            else:
                expected = vm.bytecode['records'][typ][key]
            _checked_child(vm, value, expected, span)
            if is_typed_map(typ):
                copied = ValueMap.from_mapping(items).set(key, value)
            elif isinstance(items, dict):
                copied = dict(items)
                copied[key] = value
            else:
                copied = ValueVector.from_sequence(items, packed=array_item(typ) == 'int').set(key, value)
            value = _publish_copy(vm, typ, copied, span)
        return value
    finally:
        del vm.stack[root_mark:]


def edit_collection(vm, base, method, args, result_type, span):
    """Return (new collection, method result) without changing the receiver."""
    from .vm import Ref

    mark = len(vm.stack)
    vm.stack.extend([base, *args])
    try:
        if not isinstance(base, Ref) or not vm.heap[base.address].get('immutable'):
            vm.fail('Collection method requires value storage', span)
        if vm.cancelled:
            vm.fail('Execution cancelled', span)
        typ = vm.value_type(base)
        items = vm.heap[base.address]['items']
        element = array_item(typ)
        found, removed = False, None
        changed = True
        if method in ('append', 'extend', 'pop'):
            if element is None:
                vm.fail('Array method requires an array', span)
            if method == 'append':
                _checked_child(vm, args[0], element, span)
                copied = ValueVector.from_sequence(items, packed=array_item(typ) == 'int').append(args[0])
            elif method == 'extend':
                _checked_child(vm, args[0], typ, span)
                added = vm.heap[args[0].address]['items']
                changed = bool(added)
                copied = ValueVector.from_sequence(items, packed=array_item(typ) == 'int').extend(added) if changed else items
            else:
                changed = bool(items)
                copied = ValueVector.from_sequence(items, packed=array_item(typ) == 'int').pop() if changed else items
                if changed:
                    found, removed = True, items[-1]
        elif method == 'remove':
            if not is_typed_map(typ):
                vm.fail('Map removal requires a map', span)
            key_type, element = map_parts(typ)
            key = map_keys.wrap(vm, args[0], key_type, span)
            changed = key in items
            copied = ValueMap.from_mapping(items).remove(key) if changed else items
            if changed:
                found, removed = True, items[key]
        else:
            vm.fail('Unknown collection method', span)
        if method in ('pop', 'remove') and result_type != 'Option<' + element + '>':
            vm.fail('Collection method result disagrees with element type', span)
        updated = _publish_copy(vm, typ, copied, span) if changed else base
        vm.stack.append(updated)
        result = 0
        if method in ('pop', 'remove'):
            result = vm.make_enum(result_type, 'Some' if found else 'None',
                                  ['value'] if found else [], [removed] if found else [], span)
        return updated, result
    finally:
        del vm.stack[mark:]
