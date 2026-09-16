"""Versioned value types and deterministic builtin signatures."""

import re

from .typeexpr import is_value_type, parse_type

ERROR_FIELDS = {
    name: "str"
    for name in ("kind", "code", "message", "path", "operation", "expected", "actual")
}


def is_record(name):
    if not isinstance(name, str):
        return False
    try:
        value = parse_type(name)
    except ValueError:
        return False
    return value.kind == "nominal" and (
        value.name in ("Error", "Option")
        or re.fullmatch(r"@[0-9]{1,5}:[0-9]{1,5}", value.name) is not None
    )


def base_type(name):
    while name.endswith(("[]", "{}")):
        name = name[:-2]
    return name


SCALARS = ("int", "float64", "bool", "str")
BUILTINS = [
    "len",
    "append",
    "pop",
    "slice",
    "string",
    "parseInt",
    "assert",
    "split",
    "join",
    "input",
    "trim",
    "replace",
    "keys",
    "has",
    "delete",
    "fail",
    "jsonDecode",
    "jsonParse",
    "jsonStringify",
    "jsonKind",
    "jsonGet",
    "jsonAt",
    "jsonAsInt",
    "jsonAsString",
    "jsonAsBool",
    "jsonFrom",
    "jsonNull",
    "jsonKeys",
    "readText",
    "writeText",
    "float64",
    "int",
    "parseFloat64",
    "jsonAsFloat64",
    "lookup",
    "unicodeCategory",
    "utf8Encode",
    "utf8Decode",
    "float64Bytes",
    "float64FromBytes",
    "crc32",
    "zlibCompress",
    "zlibDecompress",
    "readBytes",
    "writeBytes",
    "extend",
    "arrayRepeat",
]


def valid_type(name, parameters=frozenset()):
    if not isinstance(name, str):
        return False
    try:
        value = parse_type(name)
    except ValueError:
        return False
    # Only executable type forms are admitted at the VM boundary. New structures
    # are enabled here together with their checker, pixel and runtime support.
    return is_value_type(value, parameters) and all(
        item.kind in ("scalar", "array", "map", "function")
        or item.kind == "parameter"
        and item.name in parameters
        or item.kind == "nominal"
        and is_record(item.name)
        for item in value.walk()
    )


# Shared by signature checking and the VM instruction dispatcher.
BUILTIN_ARITIES = {
    "unicodeCategory": 1,
    "utf8Encode": 1,
    "utf8Decode": 1,
    "float64Bytes": 1,
    "float64FromBytes": 1,
    "crc32": 1,
    "zlibCompress": 1,
    "zlibDecompress": 2,
    "lookup": 2,
    "float64": 1,
    "int": 1,
    "parseFloat64": 1,
    "jsonAsFloat64": 1,
    "len": 1,
    "append": 2,
    "extend": 2,
    "arrayRepeat": 2,
    "pop": 1,
    "slice": 3,
    "string": 1,
    "parseInt": 1,
    "assert": 1,
    "split": 2,
    "join": 2,
    "input": 0,
    "trim": 1,
    "replace": 3,
    "keys": 1,
    "has": 2,
    "delete": 2,
    "fail": 1,
    "jsonParse": 1,
    "jsonStringify": 1,
    "jsonKind": 1,
    "jsonGet": 2,
    "jsonAt": 2,
    "jsonAsInt": 1,
    "jsonAsString": 1,
    "jsonAsBool": 1,
    "jsonFrom": 1,
    "jsonNull": 0,
    "jsonKeys": 1,
    "readBytes": 2,
    "writeBytes": 2,
    "readText": 1,
    "writeText": 2
}


def builtin_type(name, args):
    def require(condition, message):
        if not condition:
            raise ValueError(message)

    require(len(args) == BUILTIN_ARITIES[name], f"{name} expects {BUILTIN_ARITIES[name]} arguments")
    binary_signatures = {
        "utf8Encode": ("str", "int[]"), "utf8Decode": ("int[]", "str"),
        "float64Bytes": ("float64", "int[]"), "float64FromBytes": ("int[]", "float64"),
        "crc32": ("int[]", "int"), "zlibCompress": ("int[]", "int[]"),
    }
    if name in binary_signatures:
        source, result = binary_signatures[name]
        require(args == [source], f"{name} expects {source}")
        return result
    if name == "zlibDecompress":
        require(args == ["int[]", "int"], "zlibDecompress expects bytes and an output limit")
        return "int[]"
    if name in ("readBytes", "writeBytes"):
        require(args == (["str", "int"] if name == "readBytes" else ["str", "int[]"]), f"Invalid {name} arguments")
        return "int[]" if name == "readBytes" else "bool"
    if name == "lookup":
        require(
            args[0].endswith("{}") and args[1] == "str",
            "lookup expects a map and string key",
        )
        return "Option<" + args[0][:-2] + ">"
    if name in ("float64", "int"):
        require(args[0] in ("int", "float64"), f"{name} expects a numeric value")
        return name
    if name == "parseFloat64":
        require(args == ["str"], "parseFloat64 expects a string")
        return "float64"
    if name == "input":
        return "str"
    if name in ("readText", "writeText"):
        require(
            args == (["str"] if name == "readText" else ["str", "str"]),
            f"{name} expects string arguments",
        )
        return "str" if name == "readText" else "bool"
    if name == "jsonNull":
        return "json"
    if name == "jsonFrom":
        require(valid_type(args[0]), "jsonFrom expects a value")
        return "json"
    if name.startswith("json"):
        signatures = {
            "jsonParse": (["str"], "json"),
            "jsonStringify": (["json"], "str"),
            "jsonKind": (["json"], "str"),
            "jsonGet": (["json", "str"], "json"),
            "jsonAt": (["json", "int"], "json"),
            "jsonAsInt": (["json"], "int"),
            "jsonAsFloat64": (["json"], "float64"),
            "jsonAsString": (["json"], "str"),
            "jsonAsBool": (["json"], "bool"),
            "jsonKeys": (["json"], "str[]"),
        }
        expected, result = signatures[name]
        require(args == expected, f"{name} expects {expected}")
        return result
    first = args[0]
    if name == "fail":
        require(first == "str", "fail expects a message string")
        return "unit"
    if name in ("keys", "has", "delete"):
        require(first.endswith("{}"), f"{name} expects a map")
        if name != "keys":
            require(args[1] == "str", "Map key must be a string")
        return "str[]" if name == "keys" else "bool"
    if name == "len" and (first.endswith("{}") or first == "json"):
        return "int"
    if name in ("len", "slice"):
        require(
            first == "str" or first.endswith("[]"), f"{name} expects a string or array"
        )
        if name == "slice":
            require(args[1:] == ["int", "int"], "slice bounds must be integers")
        return "int" if name == "len" else first
    if name in ("extend", "arrayRepeat"):
        require(first.endswith("[]"), f"{name} expects an array")
        require(args[1] == (first if name == "extend" else "int"),
                f"Invalid {name} argument type")
        return first
    if name in ("append", "pop"):
        require(first.endswith("[]"), f"{name} expects an array")
        if name == "append":
            require(
                args[1] == first[:-2], "append value must match the array element type"
            )
        return first if name == "append" else first[:-2]
    if name == "string":
        require(first in SCALARS, "string conversion expects a scalar")
        return "str"
    if name == "parseInt":
        require(first == "str", "parseInt expects a string")
        return "int"
    if name == "assert":
        require(first == "bool", "assert expects a bool")
        return "bool"
    if name == "split":
        require(args == ["str", "str"], "split expects two strings")
        return "str[]"
    if name == "unicodeCategory":
        require(args == ["str"], "unicodeCategory expects a string")
        return "str"
    if name == "trim":
        require(args == ["str"], "trim expects a string")
        return "str"
    if name == "replace":
        require(args == ["str", "str", "str"], "replace expects three strings")
        return "str"
    if name == "join":
        require(args == ["str[]", "str"], "join expects a string array and separator")
        return "str"
