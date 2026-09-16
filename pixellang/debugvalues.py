"""Bounded, lazy debugger views over a VM at one logical time."""

import json
from itertools import islice

from .jsonvalue import JsonValue
from .model import PixelError
from .typeexpr import parse_type
from .vm import Ref


def display_type(timeline, typ):
    if typ == "uninitialized":
        return typ

    def name_of(name):
        debug=getattr(timeline.compiled,"debug",None)
        if debug and name in debug["types"]:
            source,_,symbol=debug["types"][name].rpartition(':')
            if source==timeline.source.get('metadata',{}).get('filename'):
                return symbol
            return source.removesuffix('.pxl').replace('/','.')+'.'+symbol
        if not name.startswith("@"):
            return name
        module, identifier = name[1:].split(":", 1)
        document = (
            timeline.source
            if module in ("0", "main")
            else timeline.source.get("bundle", {}).get("modules", {}).get(module, {})
        )
        metadata = document.get("metadata", {})
        symbol = metadata.get("symbols", {}).get(identifier, name)
        filename = metadata.get("filename", "")
        prefix = filename.removesuffix(".pxl").replace("/", ".")
        return (
            f"{prefix}.{symbol}" if module not in ("0", "main") and prefix else symbol
        )

    return parse_type(typ).display(name_of)


def children(vm, value):
    value = vm.binding_value(value)
    if isinstance(value, Ref):
        cell = vm.heap[value.address]
        value = cell["items"]
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
    if isinstance(value, JsonValue):
        if value.tag == "object":
            return iter(value.data), True, len(value.data)
        if value.tag == "array":
            return enumerate(value.data), False, len(value.data)
        return iter(()), False, 0
    if isinstance(value, dict):
        return iter(value.items()), True, len(value)
    if isinstance(value, list):
        return enumerate(value), False, len(value)
    return iter(()), False, 0


def inspect_values(timeline, args):
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
        debug=getattr(timeline.compiled,'debug',None)
        if debug:
            function=timeline.compiled.bytecode['functions'][function_key]
            start=len(function['params'])
            names=debug['functions'][function_key]['names'][start:start+len(function.get('captures',[]))]
            capture_names={f'$capture{i}':name for i,name in enumerate(names)}
        else:
            node = timeline.compiled.modules["main"].data["_function_instances"][function_key]
            module = function_key.split(":", 1)[0]
            document = timeline.source if module == "main" else timeline.source["bundle"]["modules"][module]
            symbols = document.get("metadata", {}).get("symbols", {})
            capture_names = {
                f"$capture{i}": symbols.get(str(name), str(name))
                for i, name in enumerate(node.data.get("_capture_names", []))
            }
    result = []
    for key, item in islice(items, start, start + count):
        item = vm.binding_value(item)
        _, member_named, size = children(vm, item)
        typ = vm.value_type(item)
        readable_type = display_type(timeline, typ)
        if isinstance(item, Ref):
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
        result.append(
            {
                "name": capture_names.get(key, str(key)),
                "value": summary[:256],
                "type": readable_type,
                "runtime_type": typ,
                "path": path + [key] if size else None,
                "namedVariables" if member_named else "indexedVariables": size,
            }
        )
    return {"variables": result, "total": total, "named": named}
