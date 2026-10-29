"""Bounded, lazy debugger views over a VM at one logical time."""
from .value_map import ValueMap


import json
from itertools import islice

from .jsonvalue import JsonValue
from . import spatial_values as spatial
from .native_flow import FlowHandle
from .flow_debug import flow_values, instance_values
from .model import PixelError
from .typeexpr import parse_type, is_typed_map, map_parts
from .vm import Ref
from .value_vector import ValueVector


def display_type(timeline, typ):
    if typ == "uninitialized":
        return typ

    debug=timeline.compiled.debug
    entry=(timeline.source_project or {}).get('entry')
    if entry is None:
        entry=next((info['source'] for info in debug['functions'].values() if info['module']=='main'),None)

    def name_of(name):
        if name not in debug['types']:
            return name
        source,_,symbol=debug['types'][name].rpartition(':')
        if source==entry:
            return symbol
        return source.removesuffix('.pxl').replace('/','.')+'.'+symbol

    return parse_type(typ).display(name_of)


def children(vm, value):
    value = vm.binding_value(value)
    if isinstance(value, Ref):
        cell = vm.heap[value.address]
        value = cell["items"]
        if is_typed_map(cell['type']):
            if map_parts(cell['type'])[0] == 'str':
                return ((key.value, item) for key, item in value.items()), True, len(value)
            return enumerate({'key': key.value, 'value': item} for key, item in value.items()), False, len(value)
        if parse_type(cell["type"]).kind == "function":
            return (
                ((key, item) for key, item in value.items() if key != "$function"),
                True,
                len(value) - 1,
            )
        if cell["type"] in vm.bytecode.get("enums", {}):
            return (
                ((key, item) for key, item in value.items() if key != "$variant"),
                True,
                len(value) - 1,
            )
    if isinstance(value, spatial.FrozenValue):
        if is_typed_map(value.type_name):
            if map_parts(value.type_name)[0] == 'str':
                return iter(value.items), True, len(value.items)
            return enumerate({'key': key, 'value': item} for key, item in value.items), False, len(value.items)
        if value.type_name.endswith('[]'):
            return enumerate(value.items), False, len(value.items)
        enum = value.type_name in vm.bytecode.get('enums', {})
        items = tuple((key, item) for key, item in value.items if not enum or key != '$variant')
        return iter(items), True, len(items)
    if isinstance(value, FlowHandle):
        value = instance_values(vm, value)
    elif isinstance(value, spatial.SpatialValue):
        value = dict(zip(('x', 'y', 'z'), value.xyz)) | dict(frame=value.frame.identity, unit=value.frame.unit)
    elif isinstance(value, spatial.Transform):
        value = dict(source=value.source.identity, target=value.target.identity, rotation=value.rotation,
                     translation=spatial.SpatialValue('Vec3', value.target, value.translation))
    elif isinstance(value, spatial.SpaceValue):
        value = dict(identity=value.identity, frame=value.frame.identity, unit=value.frame.unit,
                     version=value.version, entities=value.entities, links=value.links)
    elif isinstance(value, (spatial.EntityId, spatial.LinkId)):
        value = dict(key=value.key, owner=value.owner)
    elif isinstance(value, spatial.LinkValue):
        value = dict(id=value.id, owner=value.owner, source=value.source_id, target=value.target_id,
                     payload=value.payload)
    elif isinstance(value, spatial.EntityValue):
        value = dict(id=value.id, owner=value.owner, position=value.position, payload=value.payload)
    if isinstance(value, JsonValue):
        if value.tag == "object":
            return iter(value.data), True, len(value.data)
        if value.tag == "array":
            return enumerate(value.data), False, len(value.data)
        return iter(()), False, 0
    if isinstance(value, (dict, ValueMap)):
        return iter(value.items()), True, len(value)
    if isinstance(value, (list, tuple, bytearray, ValueVector)):
        return enumerate(value), False, len(value)
    return iter(()), False, 0


def resolve_value(timeline, args):
    if args.get("z") != timeline.cursor:
        raise PixelError("debug", "Variable reference belongs to a different time")
    vm = timeline.vm if timeline.cursor == len(timeline.events) else timeline.replay_vm
    path = args.get("path", [])
    if not isinstance(path, list) or not 1 <= len(path) <= 130:
        raise PixelError("debug", "Invalid variable path")
    root = path[0]
    if root == "frame" and len(path) >= 2 and type(path[1]) is int:
        frames = list(reversed(vm.call_stack))
        if not 0 <= path[1] < len(frames):
            raise PixelError("debug", "Unknown frame")
        value, remainder = frames[path[1]].memory, path[2:]
    elif root == "flow" and len(path) >= 2:
        value, remainder = flow_values(vm, path[1]), path[2:]
    elif root == "stack":
        value, remainder = vm.stack, path[1:]
    elif root == "heap":
        value, remainder = {str(key): Ref(key) for key in vm.heap}, path[1:]
    else:
        raise PixelError("debug", "Invalid variable root")
    missing = object()
    for key in remainder:
        items, _, _ = children(vm, value)
        match = next(
            (item for name, item in items if type(name) is type(key) and name == key),
            missing,
        )
        if match is missing:
            raise PixelError("debug", "Variable member no longer exists")
        value = match
    return vm, path, vm.binding_value(value)


def inspect_values(timeline, args):
    vm, path, value = resolve_value(timeline, args)
    start, count = args.get("start", 0), args.get("count", 100)
    if (
        type(start) is not int
        or start < 0
        or type(count) is not int
        or not 1 <= count <= 1000
    ):
        raise PixelError("debug", "Invalid variable page")
    items, named, total = children(vm, value)
    parent = vm.binding_value(value)
    capture_names = {}
    if isinstance(parent, Ref) and parse_type(vm.value_type(parent)).kind == "function":
        function_key = vm.heap[parent.address]["items"]["$function"]
        debug=timeline.compiled.debug
        function=timeline.compiled.bytecode['functions'][function_key]
        capture_start=len(function['params'])
        names=debug['functions'][function_key]['names'][capture_start:capture_start+len(function.get('captures',[]))]
        capture_names={f'$capture{i}':name for i,name in enumerate(names)}
    result = []
    for key, item in islice(items, start, start + count):
        result.append(describe_value(timeline, vm, item, path + [key], capture_names.get(key, str(key))))
    return {"variables": result, "total": total, "named": named}


def describe_value(timeline, vm, item, path, name):
    item = vm.binding_value(item)
    _, member_named, size = children(vm, item)
    synthetic = isinstance(item, (dict, ValueMap, list, tuple, bytearray, ValueVector))
    typ = item.type_name if isinstance(item, spatial.FrozenValue) else vm.value_type(item)
    readable_type = "" if synthetic else display_type(timeline, typ)
    if synthetic:
        typ = ""
        summary = f"{'Fields' if member_named else 'Items'} ({size})"
    elif isinstance(item, spatial.FrozenValue):
        tag = dict(item.items).get('$variant') if typ in vm.bytecode.get('enums', {}) else None
        label = f"{readable_type}.{tag}" if tag is not None else readable_type
        summary = f"{label} (snapshot, {size})"
    elif isinstance(item, spatial.SpatialValue):
        summary = f"{readable_type} ({', '.join(str(v) for v in item.xyz)})"
    elif isinstance(item, spatial.Transform):
        summary = readable_type
    elif isinstance(item, spatial.SpaceValue):
        summary = f"{readable_type} {item.identity} ({len(item.entities)} entities, {len(item.links)} links)"
    elif isinstance(item, (spatial.EntityId, spatial.LinkId)):
        summary = f"{readable_type} {item.owner}/{item.key}"
    elif isinstance(item, spatial.LinkValue):
        summary = f"{readable_type} #{item.identity} ({item.source} → {item.target})"
    elif isinstance(item, spatial.EntityValue):
        summary = f"{readable_type} #{item.identity}"
    elif isinstance(item, FlowHandle):
        instance = vm.flow_runtime.instances.get(item.identity)
        summary = f"{readable_type} #{item.identity}" + (f" (step {instance.steps})" if instance else "")
    elif isinstance(item, Ref):
        variant = (
            vm.heap[item.address]["items"].get("$variant")
            if typ in vm.bytecode.get("enums", {})
            else None
        )
        label = (
            f"{readable_type}.{variant}" if variant is not None else readable_type
        )
        summary = f"{label} @{item.address} ({size})"
    elif isinstance(item, JsonValue):
        summary = (
            f"json {item.tag} ({size})"
            if item.tag in ("array", "object")
            else item.data
            if item.tag == "number"
            else json.dumps(item.data, ensure_ascii=False)
        )
    else:
        summary = (
            "<uninitialized>"
            if item is None
            else json.dumps(item, ensure_ascii=False)
        )
    return {
        "name": name, "value": summary[:256], "type": readable_type,
        "runtime_type": typ, "path": path if size else None,
        "namedVariables" if member_named else "indexedVariables": size,
    }
