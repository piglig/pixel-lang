"""A bounded stack VM. Trace/state are independent of source and executable."""

import math
import re
import struct
import unicodedata
import zlib
from copy import deepcopy
from dataclasses import dataclass, field
from functools import lru_cache

from . import jsonvalue
from .fileaccess import FileAccess
from .jsonvalue import JsonValue
from .model import PixelError
from .typeexpr import parse_type
from .typesys import (
    BUILTINS,
    BUILTIN_ARITIES,
    ERROR_FIELDS,
    base_type,
    builtin_type,
    is_record,
    valid_type,
)

DATA_OPS = {"MAP", "TRY", "END_TRY", "ITER_SNAPSHOT", "RECORD", "FIELD", "FIELD_SET"}
DATA_OPS |= {"ENUM", "ENUM_IS", "ENUM_GET"}
COLLECTION_OPS = {"ARRAY", "INDEX", "INDEX_SET", "BUILTIN", "CONCAT"}
OPS = {
    "JSON_DECODE",
    "TRAP",
    "PUSH",
    "LOAD",
    "STORE",
    "BIND",
    "CAPTURE",
    "ADD",
    "SUB",
    "MUL",
    "DIV",
    "MOD",
    "NEG",
    "NOT",
    "EQ",
    "NE",
    "LT",
    "LE",
    "GT",
    "GE",
    "DUP",
    "DUP2",
    "POP",
    "PRINT",
    "CALL",
    "FUNCTION",
    "CLOSURE",
    "INVOKE",
    "RETURN",
    "JUMP",
    "JUMP_IF_FALSE",
    "JUMP_IF_TRUE",
}
LIMIT = 2**63
SCALAR_TYPES = {int: "int", float: "float64", bool: "bool", str: "str"}
# These containers cannot contain heap references. Avoid copying/scanning PNG
# channel arrays and text tables during every garbage collection.
LEAF_CONTAINERS = frozenset(typ + suffix for typ in ("int", "float64", "bool", "str", "json") for suffix in ("[]", "{}"))


@lru_cache(maxsize=4096)
def _builtin_result(name, argument_types):
    """Pure signature validation; values, heap state and errors are never cached."""
    return builtin_type(name, list(argument_types))


def validate_bytecode(bc):
    def fail(msg):
        raise PixelError("bytecode", msg)

    if (
        not isinstance(bc, dict)
        or bc.get("magic") != "PIXELVM"
        or bc.get("vm_version") != "0.8"
        or bc.get("language_version") != bc.get("vm_version")
    ):
        fail("Unsupported executable format/version")

    records = bc.get("records", {})
    enums = bc.get("enums", {})
    if not isinstance(records, dict) or len(records) > 65536:
        fail("Invalid record schemas")
    if not isinstance(enums, dict) or len(enums) > 256 or set(enums) & set(records):
        fail("Invalid enum schemas")

    if records.get("Error") != ERROR_FIELDS:
        fail("Missing or invalid Error schema")

    def executable_type(t):
        return valid_type(t) and all(
            item.spelling() in records or item.spelling() in enums
            for item in parse_type(t).walk()
            if item.kind == "nominal"
        )

    for typ, schema in records.items():
        if (
            not is_record(typ)
            or not isinstance(schema, dict)
            or len(schema) > 1024
            or any(
                not isinstance(name, str)
                or not name.isidentifier()
                or len(name) > 128
                or not executable_type(field_type)
                for name, field_type in schema.items()
            )
        ):
            fail("Invalid record schema field or type")
    for typ, variants in enums.items():
        if (
            not is_record(typ)
            or not valid_type(typ)
            or not isinstance(variants, dict)
            or not 1 <= len(variants) <= 256
        ):
            fail("Invalid enum schema")
        for tag, fields in variants.items():
            if (
                not isinstance(tag, str)
                or not tag.isidentifier()
                or len(tag) > 128
                or not isinstance(fields, dict)
                or len(fields) > 1024
                or any(
                    not isinstance(name, str)
                    or not name.isidentifier()
                    or len(name) > 128
                    or not executable_type(t)
                    for name, t in fields.items()
                )
            ):
                fail("Invalid enum variant payload")
        value = parse_type(typ)
        if value.name == "Option" and variants != {
            "None": {},
            "Some": {"value": value.args[0].spelling()},
        }:
            fail("Invalid Option schema")

    constants, functions, entry = (
        bc.get("constants"),
        bc.get("functions"),
        bc.get("entry"),
    )
    if (
        not isinstance(constants, list)
        or not isinstance(functions, dict)
        or not isinstance(entry, list)
        or len(entry) != 1
    ):
        fail("Invalid executable sections")
    if any(not isinstance(f, dict) for f in functions.values()):
        fail("Invalid function")
    for c in constants:
        if not isinstance(c, dict) or c.get("type") not in (
            "int",
            "float64",
            "bool",
            "str",
        ):
            fail("Invalid constant")
        v = c.get("value")
        if (c["type"] == "int" and (type(v) is not int or not -LIMIT <= v < LIMIT)) or (
            c["type"] == "bool" and type(v) is not bool
        ):
            fail("Constant type/range mismatch")
        if c["type"] == "str" and (type(v) is not str or len(v) > 1_000_000):
            fail("Invalid string constant")
        if c["type"] == "float64" and (type(v) is not float or not math.isfinite(v)):
            fail("Invalid finite float64 constant")
    for name in entry:
        if not isinstance(name, str) or name not in functions:
            fail("Unknown entry function")
    for name, f in functions.items():
        if not isinstance(f, dict):
            fail("Invalid function")
        params, loc, code = f.get("params"), f.get("locals"), f.get("instructions")
        if (
            not isinstance(params, list)
            or any(not executable_type(t) for t in params)
            or type(loc) is not int
            or not isinstance(f.get("captures", []), list)
            or any(not executable_type(t) for t in f.get("captures", []))
            or not len(params) + len(f.get("captures", [])) <= loc <= 65536
            or not (f.get("result") == "unit" or executable_type(f.get("result")))
        ):
            fail("Invalid function signature/memory size")
        if not isinstance(code, list) or not code or len(code) > 1_000_000:
            fail("Invalid instructions")
        for ins in code:
            if (
                not isinstance(ins, dict)
                or not isinstance(ins.get("op"), str)
                or ins.get("op") not in (OPS | COLLECTION_OPS | DATA_OPS)
            ):
                fail("Unknown instruction")
            span = ins.get("span")
            if span is not None:
                if not isinstance(span, dict) or not isinstance(
                    span.get("source"), str
                ):
                    fail("Invalid source provenance")
                points = span.get("points")
                if not isinstance(points, list) or any(
                    not isinstance(p, list)
                    or len(p) != 2
                    or any(type(v) is not int or v < 0 for v in p)
                    for p in points
                ):
                    fail("Invalid source provenance points")
                for key in ("min", "max"):
                    if (
                        not isinstance(span.get(key), list)
                        or len(span[key]) != 2
                        or any(type(v) is not int for v in span[key])
                    ):
                        fail("Invalid source provenance bounds")
            op, arg = ins["op"], ins.get("arg")
            if (
                op in ("PUSH", "LOAD", "STORE", "BIND", "CAPTURE")
                or op.startswith("JUMP")
                or op == "TRY"
            ):
                bound = (
                    len(constants)
                    if op == "PUSH"
                    else loc
                    if op in ("LOAD", "STORE", "BIND", "CAPTURE")
                    else len(code)
                )
                if type(arg) is not int or not 0 <= arg < bound:
                    fail(f"Invalid {op} operand")
            elif op in ("CALL", "FUNCTION", "CLOSURE") and (
                not isinstance(arg, str) or arg not in functions
            ):
                fail("Unknown call target")
            elif op in ("CALL", "FUNCTION") and functions[arg].get("captures"):
                fail("Captured function requires closure construction")
            elif op == "INVOKE" and (
                not executable_type(arg) or parse_type(arg).kind != "function"
            ):
                fail("Invalid indirect call signature")
            elif op in ("ENUM", "ENUM_IS", "ENUM_GET"):
                if (
                    not isinstance(arg, dict)
                    or not isinstance(arg.get("type"), str)
                    or arg["type"] not in enums
                    or not isinstance(arg.get("variant"), str)
                    or arg["variant"] not in enums[arg["type"]]
                ):
                    fail("Invalid enum operand")
                schema = enums[arg["type"]][arg["variant"]]
                if op == "ENUM" and (
                    not isinstance(arg.get("fields"), list)
                    or any(not isinstance(f, str) for f in arg["fields"])
                    or len(arg["fields"]) != len(set(arg["fields"]))
                    or set(arg["fields"]) != set(schema)
                ):
                    fail("Invalid enum payload fields")
                if op == "ENUM_GET" and (
                    not isinstance(arg.get("field"), str) or arg["field"] not in schema
                ):
                    fail("Invalid enum payload projection")
            elif op in ("FIELD", "FIELD_SET"):
                if not isinstance(arg, str) or not arg.isidentifier() or len(arg) > 128:
                    fail("Invalid record field operand")
            elif op == "ITER_SNAPSHOT":
                if arg not in ("single", "pair"):
                    fail("Invalid iteration mode")
            elif op == "RECORD":
                if (
                    not isinstance(arg, dict)
                    or not isinstance(arg.get("type"), str)
                    or arg["type"] not in records
                    or not isinstance(arg.get("fields"), list)
                    or any(not isinstance(name, str) for name in arg["fields"])
                ):
                    fail("Invalid record constructor")
                if len(arg["fields"]) != len(set(arg["fields"])) or set(
                    arg["fields"]
                ) != set(records[arg["type"]]):
                    fail("Record constructor fields differ from schema")
            elif op in ("ARRAY", "MAP"):
                if (
                    not isinstance(arg, dict)
                    or not executable_type(arg.get("type"))
                    or not arg["type"].endswith("{}" if op == "MAP" else "[]")
                    or type(arg.get("count")) is not int
                    or not 0 <= arg["count"] <= 1_000_000
                    or op == "MAP"
                    and arg["count"] % 2 != 0
                ):
                    fail("Invalid array operand")
            elif op == "JSON_DECODE":
                if not executable_type(arg) or base_type(arg) == "Error":
                    fail("Invalid JSON decode target")
            elif op == "BUILTIN" and (arg not in BUILTINS or arg == "jsonDecode"):
                fail("Unknown builtin")
    if any(
        functions[n]["params"]
        or functions[n].get("captures")
        or functions[n]["result"] != "unit"
        for n in entry
    ):
        fail("Entry functions must have no parameters and return unit")
    return bc


@dataclass(frozen=True)
class Ref:
    address: int


@dataclass
class Frame:
    function: str
    pc: int
    memory: list
    base: int
    handlers: list = field(default_factory=list)


class VM:
    def __init__(
        self,
        bytecode,
        max_steps=100_000,
        trace=False,
        max_depth=256,
        input_text="",
        file_access=None,
        *,
        max_heap_items=1_000_000,
        max_heap_objects=100_000,
        max_output_chars=1_000_000,
    ):
        self.bytecode = deepcopy(validate_bytecode(bytecode))
        if type(max_steps) is not int or max_steps < 1:
            raise PixelError("runtime", "Step budget must be positive")
        if type(max_depth) is not int or max_depth < 1:
            raise PixelError("runtime", "Call depth budget must be positive")
        for name, limit in (("Heap item", max_heap_items), ("Heap object", max_heap_objects), ("Output", max_output_chars)):
            if type(limit) is not int or limit < 1:
                raise PixelError("runtime", f"{name} budget must be positive")
        self.max_heap_items = max_heap_items
        self.max_heap_objects = max_heap_objects
        self.max_steps, self.max_depth, self.record_trace = max_steps, max_depth, trace
        if not isinstance(input_text, str) or len(input_text) > 1_000_000:
            raise PixelError(
                "runtime", "Input must be a string of at most one million characters"
            )
        self.input_text = input_text
        self.file_access = file_access if file_access is not None else FileAccess()
        self.effect_cursor = 0
        self.heap_items = 0
        self.next_address = 0
        self.allocations_since_gc = 0
        self.collections = 0
        self.cancelled = False
        self.output_budget = max_output_chars
        self.stack, self.call_stack, self.heap, self.output, self.trace = (
            [],
            [],
            {},
            [],
            [],
        )
        self.steps, self.entry_index, self.halted = 0, 0, False
        self.error = None
        self._start_entry()

    @property
    def constants(self):
        return self.bytecode["constants"]

    @property
    def pc(self):
        return self.call_stack[-1].pc if self.call_stack else None

    @property
    def memory(self):
        return self.call_stack[-1].memory if self.call_stack else []

    def _start_entry(self):
        if self.entry_index == len(self.bytecode["entry"]):
            self.halted = True
            return
        name = self.bytecode["entry"][self.entry_index]
        self.entry_index += 1
        self._call(name, [])

    def _call(self, name, args):
        if len(self.call_stack) >= self.max_depth:
            self.fail("Call stack limit exceeded")
        fn = self.bytecode["functions"][name]
        self.call_stack.append(
            Frame(name, 0, args + [None] * (fn["locals"] - len(args)), len(self.stack))
        )

    def fail(self, message, span=None):
        raise PixelError("runtime", message, span)

    def fail_data(self, message, span=None, **details):
        raise PixelError(
            "data",
            message[:256],
            span,
            **details,
        )

    def pop(self, span):
        if len(self.stack) <= self.call_stack[-1].base:
            self.fail("Operand stack underflow", span)
        return self.stack.pop()

    def value_type(self, value):
        if isinstance(value, JsonValue):
            return "json"
        if isinstance(value, Ref):
            return self.heap[value.address]["type"]
        return SCALAR_TYPES.get(type(value), "uninitialized")

    def typed(self, value, typ, span):
        if self.value_type(value) != typ:
            self.fail(f"Expected runtime {typ}", span)
        return value

    def snapshot_value(self, value):
        if isinstance(value, JsonValue):
            summary = {"type": "json", "kind": value.tag}
            if value.tag in ("array", "object"):
                summary["length"] = len(value.data)
            elif isinstance(value.data, str):
                summary["value"] = value.data[:160]
                summary["truncated"] = len(value.data) > 160
            else:
                summary["value"] = value.data
            return summary
        return (
            {"ref": value.address, "type": self.value_type(value)}
            if isinstance(value, Ref)
            else value
        )

    def materialize(self, value, span=None, ancestors=None):
        if isinstance(value, JsonValue):
            value = jsonvalue.stringify(value)
        cost = len(value) if type(value) is str else 20
        self.output_budget -= cost
        if self.output_budget < 0:
            self.fail("Output budget exceeded", span)
        if isinstance(value, Ref):
            ancestors = set() if ancestors is None else ancestors
            if value.address in ancestors:
                self.fail_data("Cannot print a cyclic value", span, code="value.cycle")
            if len(ancestors) >= 128:
                self.fail_data(
                    "Printed value nesting exceeds 128", span, code="value.depth"
                )
            ancestors.add(value.address)
            items = self.heap[value.address]["items"]
            try:
                if isinstance(items, dict):
                    if self.value_type(value) in self.bytecode["enums"]:
                        return {
                            "variant": items["$variant"],
                            "fields": {
                                k: self.materialize(v, span, ancestors)
                                for k, v in items.items()
                                if k != "$variant"
                            },
                        }
                    return {
                        self.materialize(k, span, ancestors): self.materialize(
                            v, span, ancestors
                        )
                        for k, v in items.items()
                    }
                return [self.materialize(v, span, ancestors) for v in items]
            finally:
                ancestors.remove(value.address)
        return value

    def allocate(self, typ, items, span, *, cell=False):
        record = is_record(typ)
        callable_value = parse_type(typ).kind == "function"
        mapped = cell or typ.endswith("{}") or record or callable_value
        roots = list(items.values()) if mapped else items
        if (
            # Scale tracing frequency with the live graph instead of rescanning a
            # compiler-sized heap after every 128 allocations. Hard limits below
            # still force collection before any allocation can exceed its budget.
            self.allocations_since_gc >= max(128, len(self.heap) // 2)
            or self.heap_items + len(items) > self.max_heap_items
            or len(self.heap) >= self.max_heap_objects
        ):
            self.collect(roots)
        if self.heap_items + len(items) > self.max_heap_items or len(self.heap) >= self.max_heap_objects:
            self.fail("Heap budget exceeded", span)
        if cell:
            if set(items) != {"value"} or self.is_cell(items["value"]):
                self.fail("Invalid captured binding cell", span)
            self.typed(items["value"], typ, span)
        elif callable_value:
            target = self.bytecode["functions"].get(items.get("$function"))
            if (
                target is None
                or typ
                != "fn(" + ",".join(target["params"]) + ")->(" + target["result"] + ")"
            ):
                self.fail("Invalid function value", span)
            captures = target.get("captures", [])
            if set(items) != {"$function"} | {
                f"$capture{i}" for i in range(len(captures))
            }:
                self.fail("Invalid closure environment", span)
            for index, expected in enumerate(captures):
                captured = items[f"$capture{index}"]
                if not self.is_cell(captured):
                    self.fail("Closure capture must be a binding cell", span)
                self.typed(captured, expected, span)
        elif record:
            if typ in self.bytecode["enums"]:
                tag = items.get("$variant")
                if not isinstance(tag, str) or tag not in self.bytecode["enums"][typ]:
                    self.fail("Invalid enum tag", span)
                schema = {"$variant": "str", **self.bytecode["enums"][typ][tag]}
            else:
                schema = self.bytecode["records"][typ]
            if set(items) != set(schema):
                self.fail("Record fields do not match schema", span)
            for name, field_type in schema.items():
                self.typed(items[name], field_type, span)
            items = {name: items[name] for name in sorted(schema)}
        else:
            for item in roots:
                self.typed(item, typ[:-2], span)
        address = self.next_address
        self.next_address += 1
        self.allocations_since_gc += 1
        self.heap[address] = {
            "type": typ,
            "items": dict(items) if mapped else list(items),
            **({"cell": True} if cell else {}),
        }
        self.heap_items += len(items)
        return Ref(address)

    def is_cell(self, value):
        return isinstance(value, Ref) and self.heap[value.address].get("cell", False)

    def binding_value(self, value):
        return (
            self.heap[value.address]["items"]["value"] if self.is_cell(value) else value
        )

    def capture_binding(self, frame, slot, span=None):
        value = frame.memory[slot]
        if value is None:
            self.fail("Capture of uninitialized binding", span)
        if not self.is_cell(value):
            value = self.allocate(
                self.value_type(value), {"value": value}, span, cell=True
            )
            frame.memory[slot] = value
        return value

    def make_enum(self, typ, tag, fields, values, span):
        schema = self.bytecode["enums"].get(typ, {}).get(tag)
        if schema is None or set(fields) != set(schema) or len(fields) != len(values):
            self.fail("Invalid enum construction", span)
        for field_name, value in zip(fields, values):
            self.typed(value, schema[field_name], span)
        return self.allocate(typ, {"$variant": tag, **dict(zip(fields, values))}, span)

    def collect(self, extra_roots=()):
        """Trace references from all live frames, operands and transient allocation roots."""
        pending = list(self.stack) + list(extra_roots)
        for frame in self.call_stack:
            pending.extend(frame.memory)
        reachable = set()
        while pending:
            value = pending.pop()
            if isinstance(value, Ref) and value.address not in reachable:
                reachable.add(value.address)
                obj = self.heap[value.address]
                if not obj.get("cell") and obj["type"] in LEAF_CONTAINERS:
                    continue
                items = obj["items"]
                pending.extend(items.values() if isinstance(items, dict) else items)
        removed = set(self.heap) - reachable
        for address in removed:
            self.heap_items -= len(self.heap.pop(address)["items"])
        self.allocations_since_gc = 0
        self.collections += 1
        return len(removed)

    def cancel(self):
        self.cancelled = True

    def checkpoint(self):
        """Internal trusted snapshot, including counters needed for identical replay."""
        keys = (
            "stack",
            "call_stack",
            "heap",
            "output",
            "heap_items",
            "next_address",
            "allocations_since_gc",
            "collections",
            "cancelled",
            "output_budget",
            "effect_cursor",
            "steps",
            "entry_index",
            "halted",
            "error",
        )
        return deepcopy({key: getattr(self, key) for key in keys})

    def restore_checkpoint(self, snapshot):
        for key, value in deepcopy(snapshot).items():
            setattr(self, key, value)
        self.trace = []

    def sequence(self, value, span):
        if isinstance(value, Ref):
            return self.heap[value.address]["items"]
        if type(value) is str:
            return value
        self.fail("Expected array or string", span)

    def decode_json(self, value, target, span):
        try:
            value = (
                jsonvalue.parse(value)
                if type(value) is str
                else self.typed(value, "json", span)
            )
        except PixelError as error:
            error.operation, error.path, error.span = "jsonDecode", "$", span
            raise
        root_start = len(self.stack)
        visited = 0

        def allocate(typ, items):
            ref = self.allocate(typ, items, span)
            self.stack.append(
                ref
            )  # Root partial containers until the whole conversion completes.
            return ref

        def convert(item, typ, path, depth):
            nonlocal visited
            visited += 1
            if depth > jsonvalue.MAX_JSON_DEPTH or visited > jsonvalue.MAX_JSON_NODES:
                self.fail_data(
                    "JSON decode structure budget exceeded",
                    span,
                    code="json.structure_limit",
                )
            try:
                if typ == "json":
                    return item
                if typ == "int":
                    return jsonvalue.as_int(item)
                if typ == "float64":
                    return jsonvalue.as_float64(item)
                if typ in ("str", "bool"):
                    return jsonvalue.require(item, "string" if typ == "str" else "bool")
                if typ.endswith("[]"):
                    values = jsonvalue.require(item, "array")
                    return allocate(
                        typ,
                        [
                            convert(v, typ[:-2], f"{path}[{i}]", depth + 1)
                            for i, v in enumerate(values)
                        ],
                    )
                if typ in self.bytecode["enums"] and parse_type(typ).name == "Option":
                    element = parse_type(typ).args[0]
                    if element.name == "Option":
                        self.fail_data(
                            "Adjacent nested Option types have ambiguous JSON representation",
                            span,
                            code="json.unsupported_type",
                        )
                    if item.tag == "null":
                        return allocate(typ, {"$variant": "None"})
                    return allocate(
                        typ,
                        {
                            "$variant": "Some",
                            "value": convert(item, element.spelling(), path, depth + 1),
                        },
                    )
                values = dict(jsonvalue.require(item, "object"))
                if typ.endswith("{}"):
                    return allocate(
                        typ,
                        {
                            key: convert(
                                v, typ[:-2], jsonvalue.field_path(path, key), depth + 1
                            )
                            for key, v in values.items()
                        },
                    )
                if typ == "Error":
                    self.fail_data(
                        "Error cannot be decoded", span, code="json.unsupported_type"
                    )
                if typ in self.bytecode["enums"]:
                    if set(values) != {"variant", "fields"}:
                        self.fail_data(
                            "Enum JSON requires exactly variant and fields",
                            span,
                            code="json.type_mismatch",
                        )
                    tag = jsonvalue.require(values["variant"], "string")
                    schema = self.bytecode["enums"][typ].get(tag)
                    if schema is None:
                        self.fail_data(
                            "Unknown JSON enum variant",
                            span,
                            code="json.unknown_variant",
                            path=jsonvalue.field_path(path, "variant"),
                            actual=tag,
                        )
                    payload = dict(jsonvalue.require(values["fields"], "object"))
                    if set(payload) != set(schema):
                        self.fail_data(
                            "Enum payload fields differ from variant schema",
                            span,
                            code="json.type_mismatch",
                            path=jsonvalue.field_path(path, "fields"),
                        )
                    return allocate(
                        typ,
                        {
                            "$variant": tag,
                            **{
                                name: convert(
                                    payload[name],
                                    field_type,
                                    jsonvalue.field_path(
                                        jsonvalue.field_path(path, "fields"), name
                                    ),
                                    depth + 1,
                                )
                                for name, field_type in schema.items()
                            },
                        },
                    )
                schema = self.bytecode["records"][typ]
                missing = sorted(
                    name
                    for name in set(schema) - set(values)
                    if parse_type(schema[name]).name != "Option"
                )
                extra = sorted(set(values) - set(schema))
                if missing:
                    self.fail_data(
                        "Missing field " + missing[0],
                        span,
                        code="json.missing_field",
                        path=jsonvalue.field_path(path, missing[0]),
                        expected=schema[missing[0]],
                        actual="missing",
                    )
                if extra:
                    self.fail_data(
                        "Unknown field " + extra[0],
                        span,
                        code="json.unknown_field",
                        path=jsonvalue.field_path(path, extra[0]),
                    )
                return allocate(
                    typ,
                    {
                        key: convert(
                            values.get(key, JsonValue("null")),
                            field_type,
                            jsonvalue.field_path(path, key),
                            depth + 1,
                        )
                        for key, field_type in schema.items()
                    },
                )
            except PixelError as error:
                if error.phase == "data" and error.operation != "jsonDecode":
                    error.path = (error.path or path)[:1024]
                    error.operation = "jsonDecode"
                    error.message = f"JSON decode at {error.path}: {error.message}"[
                        :256
                    ]
                    error.span = span
                raise

        try:
            return convert(value, target, "$", 0)
        finally:
            del self.stack[root_start:]

    def to_json(self, value, span):
        count = 0
        ancestors = set()

        def visit(item, depth=0):
            nonlocal count
            count += 1
            if count > jsonvalue.MAX_JSON_NODES or depth > jsonvalue.MAX_JSON_DEPTH:
                self.fail_data(
                    "JSON structure budget exceeded", span, code="json.structure_limit"
                )
            if not isinstance(item, Ref):
                return jsonvalue.from_host(item)
            if item.address in ancestors:
                self.fail_data(
                    "Cannot convert a cyclic value to JSON", span, code="json.cycle"
                )
            ancestors.add(item.address)
            try:
                values = self.heap[item.address]["items"]
                typ = self.value_type(item)
                if parse_type(typ).kind == "function":
                    self.fail_data(
                        "Function values cannot be encoded as JSON",
                        span,
                        code="json.unsupported_type",
                    )
                if typ in self.bytecode["enums"]:
                    tag = values["$variant"]
                    if parse_type(typ).name == "Option":
                        if parse_type(typ).args[0].name == "Option":
                            self.fail_data(
                                "Adjacent nested Option types have ambiguous JSON representation",
                                span,
                                code="json.unsupported_type",
                            )
                        return (
                            JsonValue("null")
                            if tag == "None"
                            else visit(values["value"], depth + 1)
                        )
                    return JsonValue(
                        "object",
                        (
                            ("variant", JsonValue("string", tag)),
                            (
                                "fields",
                                JsonValue(
                                    "object",
                                    tuple(
                                        (key, visit(child, depth + 1))
                                        for key, child in values.items()
                                        if key != "$variant"
                                    ),
                                ),
                            ),
                        ),
                    )
                if isinstance(values, dict):
                    return JsonValue(
                        "object",
                        tuple(
                            (key, visit(child, depth + 1))
                            for key, child in values.items()
                        ),
                    )
                return JsonValue(
                    "array", tuple(visit(child, depth + 1) for child in values)
                )
            finally:
                ancestors.remove(item.address)

        result = visit(value)
        jsonvalue.stringify(
            result
        )  # Enforce bounds even when existing JSON subtrees are shared.
        return result

    def builtin(self, name, args, span):
        try:
            _builtin_result(name, tuple(self.value_type(v) for v in args))
        except ValueError as e:
            self.fail(str(e), span)
        if name in ("readBytes", "writeBytes"):
            value = args[1]
            if name == "writeBytes":
                value = list(self.sequence(value, span))
                if any(type(byte) is not int or not 0 <= byte <= 255 for byte in value):
                    self.fail_data("Byte values must be integers from 0 to 255", span, code="bytes.range")
            elif not 0 <= value <= self.max_heap_items:
                self.fail_data("Binary read limit must fit the heap item budget", span, code="bytes.limit")
            cursor = self.effect_cursor
            self.effect_cursor += 1
            try:
                result = self.file_access.perform(name, args[0], value, cursor)
                return self.allocate("int[]", result, span) if name == "readBytes" else result
            except PixelError as error:
                error.span = span
                raise
        if name in ("readText", "writeText"):
            cursor = self.effect_cursor
            self.effect_cursor += 1
            try:
                return self.file_access.perform(
                    "read" if name == "readText" else "write",
                    args[0],
                    None if name == "readText" else args[1],
                    cursor,
                )
            except PixelError as error:
                error.span = span
                raise
        if name.startswith("json"):
            try:
                if name == "jsonParse":
                    return jsonvalue.parse(args[0])
                if name == "jsonNull":
                    return JsonValue("null")
                if name == "jsonFrom":
                    return self.to_json(args[0], span)
                value = args[0]
                if name == "jsonStringify":
                    return jsonvalue.stringify(value)
                if name == "jsonKind":
                    return value.tag
                if name == "jsonAsInt":
                    return jsonvalue.as_int(value)
                if name == "jsonAsFloat64":
                    return jsonvalue.as_float64(value)
                if name == "jsonAsString":
                    return jsonvalue.require(value, "string")
                if name == "jsonAsBool":
                    return jsonvalue.require(value, "bool")
                if name == "jsonAt":
                    items = jsonvalue.require(value, "array")
                    if not 0 <= args[1] < len(items):
                        self.fail_data(
                            "JSON array index out of bounds",
                            span,
                            code="json.index_bounds",
                        )
                    return items[args[1]]
                if name == "jsonKeys":
                    return self.allocate(
                        "str[]",
                        [key for key, _ in jsonvalue.require(value, "object")],
                        span,
                    )
                if name == "jsonGet":
                    for key, child in jsonvalue.require(value, "object"):
                        if key == args[1]:
                            return child
                    self.fail_data(
                        "Missing JSON key: " + args[1][:100],
                        span,
                        code="json.missing_key",
                        path=args[1],
                    )
            except PixelError as error:
                if error.span is None:
                    error.span = span
                raise
        if name == "fail":
            self.fail_data(args[0], span, code="user.failure")
        if name == "input":
            return self.input_text
        if name == "lookup":
            items = self.heap[args[0].address]["items"]
            typ = "Option<" + self.value_type(args[0])[:-2] + ">"
            if args[1] in items:
                return self.make_enum(typ, "Some", ["value"], [items[args[1]]], span)
            return self.make_enum(typ, "None", [], [], span)
        value = args[0]
        if name == "len":
            if isinstance(value, JsonValue):
                if value.tag not in ("array", "object", "string"):
                    self.fail_data(
                        "JSON length requires array, object or string",
                        span,
                        code="json.type_mismatch",
                    )
                return len(value.data)
            return len(self.sequence(value, span))
        if name in ("keys", "has", "delete"):
            items = self.heap[value.address]["items"]
            if name == "keys":
                return self.allocate("str[]", list(items), span)
            present = args[1] in items
            if name == "delete" and present:
                del items[args[1]]
                self.heap_items -= 1
            return present
        if name == "append":
            if self.heap_items >= self.max_heap_items:
                self.fail("Heap budget exceeded", span)
            self.heap[value.address]["items"].append(args[1])
            self.heap_items += 1
            return value
        if name in ("extend", "arrayRepeat"):
            items = self.sequence(value, span)
            if name == "extend":
                self.typed(args[1], self.value_type(value), span)
                added = self.sequence(args[1], span)
                size = len(added)
            else:
                count = self.typed(args[1], "int", span)
                if count < 0:
                    self.fail_data("Array repeat count must be nonnegative", span,
                                   code="collection.index_bounds")
                size = len(items) * count
            # Check before constructing a potentially large temporary. The
            # popped arguments remain roots if collection is necessary.
            if self.heap_items + size > self.max_heap_items:
                self.collect(args)
            if self.heap_items + size > self.max_heap_items:
                self.fail("Heap budget exceeded", span)
            if name == "extend":
                items.extend(added)
                self.heap_items += size
                return value
            return self.allocate(self.value_type(value), items * count, span)
        if name == "pop":
            items = self.heap[value.address]["items"]
            if not items:
                self.fail_data(
                    "Cannot pop an empty array", span, code="collection.empty"
                )
            self.heap_items -= 1
            return items.pop()
        if name == "slice":
            items = self.sequence(value, span)
            start, end = args[1:]
            if not 0 <= start <= end <= len(items):
                self.fail_data(
                    "Slice bounds out of range", span, code="collection.index_bounds"
                )
            result = items[start:end]
            return (
                result
                if type(value) is str
                else self.allocate(self.value_type(value), result, span)
            )
        if name == "string":
            return str(value).lower() if type(value) is bool else str(value)
        if name == "float64":
            return float(value)
        if name == "int":
            result = int(value)
            if not -LIMIT <= result < LIMIT:
                self.fail_data(
                    "Numeric conversion exceeds signed 64-bit range",
                    span,
                    code="numeric.range",
                )
            return result
        if name == "parseFloat64":
            if not re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", value):
                self.fail_data(
                    "Invalid decimal float64", span, code="numeric.invalid_text"
                )
            result = float(value)
            if not math.isfinite(result):
                self.fail_data(
                    "Float64 exceeds finite range", span, code="numeric.range"
                )
            return result
        if name == "parseInt":
            if not re.fullmatch(r"[+-]?[0-9]+", value) or len(value) > 21:
                self.fail_data(
                    "Invalid decimal integer", span, code="numeric.invalid_text"
                )
            result = int(value)
            if not -LIMIT <= result < LIMIT:
                self.fail_data(
                    "Signed 64-bit integer overflow", span, code="numeric.range"
                )
            return result
        if name == "assert":
            if not value:
                self.fail("Assertion failed", span)
            return True
        if name == "split":
            if not args[1]:
                self.fail_data(
                    "split separator cannot be empty", span, code="text.empty_separator"
                )
            if self.heap_items + value.count(args[1]) + 1 > self.max_heap_items:
                self.fail("Heap budget exceeded", span)
            return self.allocate("str[]", value.split(args[1]), span)
        if name == "utf8Encode":
            try:
                encoded = value.encode("utf-8", errors="strict")
            except UnicodeEncodeError:
                self.fail_data("Text contains an invalid Unicode scalar", span, code="text.invalid_utf8")
            return self.allocate("int[]", list(encoded), span)
        if name == "float64Bytes":
            return self.allocate("int[]", list(struct.pack(">d", value)), span)
        if name in ("crc32", "zlibCompress", "zlibDecompress"):
            items = self.sequence(value, span)
            if any(type(item) is not int or not 0 <= item <= 255 for item in items):
                self.fail_data("Byte values must be integers from 0 to 255", span, code="bytes.range")
            data = bytes(items)
            if name == "crc32":
                return zlib.crc32(data)
            if name == "zlibCompress":
                result = zlib.compress(data)
            else:
                limit = args[1]
                if not 0 <= limit <= self.max_heap_items:
                    self.fail_data("Decompression limit must fit the heap item budget", span, code="bytes.limit")
                try:
                    decoder = zlib.decompressobj()
                    result = decoder.decompress(data, limit + 1)
                except zlib.error:
                    self.fail_data("Invalid zlib stream", span, code="bytes.compression")
                if len(result) > limit:
                    self.fail_data("Decompressed data exceeds output limit", span, code="bytes.limit")
                if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                    self.fail_data("Truncated or trailing zlib stream", span, code="bytes.compression")
            return self.allocate("int[]", list(result), span)
        if name in ("utf8Decode", "float64FromBytes"):
            items = self.sequence(value, span)
            if any(type(item) is not int or not 0 <= item <= 255 for item in items):
                self.fail_data("Byte values must be integers from 0 to 255", span, code="bytes.range")
            if name == "float64FromBytes":
                if len(items) != 8:
                    self.fail_data("Float64 requires exactly eight bytes", span, code="bytes.length")
                result = struct.unpack(">d", bytes(items))[0]
                if not math.isfinite(result):
                    self.fail_data("Float64 exceeds finite range", span, code="numeric.range")
                return result
            try:
                result = bytes(items).decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                self.fail_data("Invalid UTF-8 byte sequence", span, code="text.invalid_utf8")
            if len(result) > 1_000_000:
                self.fail("String size budget exceeded", span)
            return result
        if name == "unicodeCategory":
            if len(value) != 1:
                self.fail_data(
                    "Expected one Unicode scalar", span, code="text.invalid_character"
                )
            return unicodedata.category(value)
        if name == "trim":
            return value.strip()
        if name == "replace":
            size = len(value) + value.count(args[1]) * (len(args[2]) - len(args[1]))
            if size > 1_000_000:
                self.fail("String size budget exceeded", span)
            return value.replace(args[1], args[2])
        if name == "join":
            items = self.heap[value.address]["items"]
            if sum(map(len, items)) + max(0, len(items) - 1) * len(args[1]) > 1_000_000:
                self.fail("String size budget exceeded", span)
            return args[1].join(items)
        self.fail("Unknown builtin", span)

    def state(self):
        def export(values):
            if isinstance(values, dict):
                return {k: self.snapshot_value(v) for k, v in values.items()}
            return [self.snapshot_value(v) for v in values]

        return {
            "pc": self.pc,
            "function": self.call_stack[-1].function if self.call_stack else None,
            "stack": export(self.stack),
            "memory": export(self.memory),
            "heap": {
                k: {
                    "type": v["type"],
                    "items": export(v["items"]),
                    **({"cell": True} if v.get("cell") else {}),
                }
                for k, v in self.heap.items()
            },
            "call_stack": [
                {"function": f.function, "pc": f.pc, "memory": export(f.memory)}
                for f in self.call_stack
            ],
            "steps": self.steps,
            "file_effects": self.effect_cursor,
            "halted": self.halted,
            "output": deepcopy(self.output),
            "error": self.error,
        }

    def step(self, snapshot=True):
        if self.halted:
            return self.state()
        frame = self.call_stack[-1]
        fn = self.bytecode["functions"][frame.function]
        ins = (
            fn["instructions"][frame.pc] if frame.pc < len(fn["instructions"]) else None
        )
        span = ins.get("span") if ins else None
        op, arg = (ins["op"], ins.get("arg")) if ins else ("STEP", None)
        try:
            if self.cancelled:
                self.fail("Execution cancelled", span)
            if self.steps >= self.max_steps:
                self.fail("Instruction budget exceeded", span)
            if not ins:
                self.fail("Function fell off instruction stream", span)
            old_pc = frame.pc
            frame.pc += 1
            self.steps += 1
            if op == "TRAP":
                self.fail("Function reached unreachable terminator", span)
            elif op == "PUSH":
                self.stack.append(self.constants[arg]["value"])
            elif op == "LOAD":
                if frame.memory[arg] is None:
                    self.fail("Read of uninitialized memory", span)
                self.stack.append(self.binding_value(frame.memory[arg]))
            elif op == "CAPTURE":
                self.stack.append(self.capture_binding(frame, arg, span))
            elif op == "BIND":
                frame.memory[arg] = self.pop(span)
            elif op == "STORE":
                value = self.pop(span)
                binding = frame.memory[arg]
                if self.is_cell(binding):
                    self.typed(value, self.value_type(binding), span)
                    if self.is_cell(value):
                        self.fail("Cannot assign a binding cell as a value", span)
                    self.heap[binding.address]["items"]["value"] = value
                else:
                    frame.memory[arg] = value
            elif op == "DUP":
                v = self.pop(span)
                self.stack.extend([v, v])
            elif op == "DUP2":
                right = self.pop(span)
                left = self.pop(span)
                self.stack.extend([left, right, left, right])
            elif op == "POP":
                self.pop(span)
            elif op == "PRINT":
                self.output.append(self.materialize(self.pop(span), span))
            elif op == "RECORD":
                values = [self.pop(span) for _ in arg["fields"]][::-1]
                self.stack.append(
                    self.allocate(arg["type"], dict(zip(arg["fields"], values)), span)
                )
            elif op == "ENUM":
                values = [self.pop(span) for _ in arg["fields"]][::-1]
                self.stack.append(
                    self.make_enum(
                        arg["type"], arg["variant"], arg["fields"], values, span
                    )
                )
            elif op in ("ENUM_IS", "ENUM_GET"):
                value = self.typed(self.pop(span), arg["type"], span)
                items = self.heap[value.address]["items"]
                matches = items["$variant"] == arg["variant"]
                if op == "ENUM_IS":
                    self.stack.append(matches)
                elif matches:
                    self.stack.append(items[arg["field"]])
                else:
                    self.fail("Enum payload projection requires matching variant", span)
            elif op in ("FIELD", "FIELD_SET"):
                assigned = self.pop(span) if op == "FIELD_SET" else None
                value = self.pop(span)
                typ = self.value_type(value)
                if (
                    typ not in self.bytecode["records"]
                    or arg not in self.bytecode["records"][typ]
                ):
                    self.fail("Invalid record field access", span)
                items = self.heap[value.address]["items"]
                if op == "FIELD_SET":
                    if typ == "Error":
                        self.fail("Error fields are read-only", span)
                    self.typed(assigned, self.bytecode["records"][typ][arg], span)
                    items[arg] = assigned
                else:
                    self.stack.append(items[arg])
            elif op == "JSON_DECODE":
                self.stack.append(self.decode_json(self.pop(span), arg, span))
            elif op == "ITER_SNAPSHOT":
                value = self.pop(span)
                items = self.sequence(value, span)
                typ = self.value_type(value)
                if is_record(typ):
                    self.fail("Record values are not iterable", span)
                if arg == "pair":
                    mapped = typ.endswith("{}")
                    values = list(items.values()) if mapped else list(items)
                    # Root values before allocating keys: a GC may run on either allocation.
                    self.stack.append(
                        self.allocate(
                            typ[:-2] + "[]"
                            if mapped
                            else typ
                            if typ.endswith("[]")
                            else "str[]",
                            values,
                            span,
                        )
                    )
                    self.stack.append(
                        self.allocate(
                            "str[]" if mapped else "int[]",
                            list(items) if mapped else list(range(len(items))),
                            span,
                        )
                    )
                    self.stack[-2], self.stack[-1] = self.stack[-1], self.stack[-2]
                else:
                    self.stack.append(
                        self.allocate(
                            typ if typ.endswith("[]") else "str[]", list(items), span
                        )
                    )
            elif op == "MAP":
                values = [self.pop(span) for _ in range(arg["count"])][::-1]
                items = {}
                for i in range(0, len(values), 2):
                    key = self.typed(values[i], "str", span)
                    self.typed(values[i + 1], arg["type"][:-2], span)
                    if key in items:
                        self.fail_data(
                            "Duplicate map key: " + key[:100],
                            span,
                            code="collection.duplicate_key",
                            path=key,
                        )
                    items[key] = values[i + 1]
                self.stack.append(self.allocate(arg["type"], items, span))
            elif op == "ARRAY":
                items = [self.pop(span) for _ in range(arg["count"])][::-1]
                self.stack.append(self.allocate(arg["type"], items, span))
            elif op in ("INDEX", "INDEX_SET"):
                assigned = self.pop(span) if op == "INDEX_SET" else None
                index = self.pop(span)
                value = self.pop(span)
                items = self.sequence(value, span)
                if is_record(self.value_type(value)):
                    self.fail("Record values require field access", span)
                mapped = isinstance(items, dict)
                self.typed(index, "str" if mapped else "int", span)
                if mapped:
                    if op == "INDEX" and index not in items:
                        self.fail_data(
                            "Missing map key: " + index[:100],
                            span,
                            code="collection.missing_key",
                            path=index,
                        )
                elif not 0 <= index < len(items):
                    self.fail_data(
                        f"Index {index} out of bounds for length {len(items)}",
                        span,
                        code="collection.index_bounds",
                        path=str(index),
                    )
                if op == "INDEX":
                    self.stack.append(items[index])
                else:
                    if not isinstance(value, Ref):
                        self.fail("Strings are immutable", span)
                    self.typed(assigned, self.value_type(value)[:-2], span)
                    if mapped and index not in items:
                        if self.heap_items >= self.max_heap_items:
                            self.collect([value, assigned])
                        if self.heap_items >= self.max_heap_items:
                            self.fail("Heap budget exceeded", span)
                        self.heap_items += 1
                    items[index] = assigned
            elif op == "BUILTIN":
                arity = BUILTIN_ARITIES[arg]
                args = [self.pop(span) for _ in range(arity)][::-1]
                self.stack.append(self.builtin(arg, args, span))
            elif op == "CONCAT":
                b = self.typed(self.pop(span), "str", span)
                a = self.typed(self.pop(span), "str", span)
                self.stack.append(a + b)
            elif op in ("NEG", "NOT"):
                v = self.pop(span)
                self.typed(
                    v,
                    ("float64" if type(v) is float else "int")
                    if op == "NEG"
                    else "bool",
                    span,
                )
                self.stack.append(-v if op == "NEG" else not v)
            elif op in (
                "ADD",
                "SUB",
                "MUL",
                "DIV",
                "MOD",
                "EQ",
                "NE",
                "LT",
                "LE",
                "GT",
                "GE",
            ):
                b, a = self.pop(span), self.pop(span)
                if op in ("EQ", "NE"):
                    if type(a) is not type(b):
                        self.fail("Equality operands have different types", span)
                    value = a == b if op == "EQ" else a != b
                else:
                    operand_type = (
                        "str"
                        if op in ("LT", "LE", "GT", "GE") and type(a) is str
                        else ("float64" if type(a) is float else "int")
                    )
                    self.typed(a, operand_type, span)
                    self.typed(b, operand_type, span)
                    if op in ("DIV", "MOD") and b == 0:
                        self.fail_data(
                            "Division by zero", span, code="numeric.division_by_zero"
                        )
                    if op in ("DIV", "MOD") and operand_type == "float64":
                        value = a / b if op == "DIV" else math.fmod(a, b)
                    elif op in ("DIV", "MOD"):
                        q = (abs(a) // abs(b)) * (-1 if (a < 0) != (b < 0) else 1)
                        value = q if op == "DIV" else a - q * b
                    else:
                        if op == "ADD":
                            value = a + b
                        elif op == "SUB":
                            value = a - b
                        elif op == "MUL":
                            value = a * b
                        elif op == "LT":
                            value = a < b
                        elif op == "LE":
                            value = a <= b
                        elif op == "GT":
                            value = a > b
                        else:
                            value = a >= b
                self.stack.append(value)
            elif op == "TRY":
                if len(frame.handlers) >= 128:
                    self.fail("Exception handler limit exceeded", span)
                frame.handlers.append((arg, len(self.stack)))
            elif op == "END_TRY":
                if not frame.handlers:
                    self.fail("Missing exception handler", span)
                frame.handlers.pop()
            elif op == "JUMP":
                frame.pc = arg
            elif op in ("JUMP_IF_FALSE", "JUMP_IF_TRUE"):
                condition = self.typed(self.pop(span), "bool", span)
                if condition == (op == "JUMP_IF_TRUE"):
                    frame.pc = arg
            elif op in ("FUNCTION", "CLOSURE"):
                target = self.bytecode["functions"][arg]
                typ = (
                    "fn(" + ",".join(target["params"]) + ")->(" + target["result"] + ")"
                )
                captures = (
                    [self.pop(span) for _ in target.get("captures", [])][::-1]
                    if op == "CLOSURE"
                    else []
                )
                self.stack.append(
                    self.allocate(
                        typ,
                        {
                            "$function": arg,
                            **{
                                f"$capture{i}": value
                                for i, value in enumerate(captures)
                            },
                        },
                        span,
                    )
                )
            elif op == "INVOKE":
                signature = parse_type(arg)
                args = [self.pop(span) for _ in signature.args[:-1]][::-1]
                callable_value = self.typed(self.pop(span), arg, span)
                for value, parameter in zip(args, signature.args[:-1]):
                    self.typed(value, parameter.spelling(), span)
                environment = self.heap[callable_value.address]["items"]
                target = environment["$function"]
                captures = [
                    environment[f"$capture{i}"]
                    for i in range(
                        len(self.bytecode["functions"][target].get("captures", []))
                    )
                ]
                self._call(target, args + captures)
            elif op == "CALL":
                target = self.bytecode["functions"][arg]
                args = [self.pop(span) for _ in target["params"]][::-1]
                for v, t in zip(args, target["params"]):
                    self.typed(v, t, span)
                if len(self.call_stack) >= self.max_depth:
                    self.fail("Call stack limit exceeded", span)
                self._call(arg, args)
            elif op == "RETURN":
                value = self.pop(span)
                if fn["result"] != "unit":
                    self.typed(value, fn["result"], span)
                if len(self.stack) != frame.base:
                    self.fail("Unbalanced operand stack at return", span)
                self.call_stack.pop()
                if self.call_stack:
                    self.stack.append(value)
                else:
                    self._start_entry()
            if (
                self.stack
                and type(self.stack[-1]) is int
                and not -LIMIT <= self.stack[-1] < LIMIT
            ):
                self.fail("Signed 64-bit integer overflow", span)
            if (
                self.stack
                and type(self.stack[-1]) is float
                and not math.isfinite(self.stack[-1])
            ):
                self.fail("Float64 arithmetic exceeds finite range", span)
            if (
                self.stack
                and type(self.stack[-1]) is str
                and len(self.stack[-1]) > 1_000_000
            ):
                self.fail("String size budget exceeded", span)
            if self.record_trace:
                self.trace.append(
                    {
                        "step": self.steps,
                        "function": frame.function,
                        "pc": old_pc,
                        "op": op,
                        "span": span,
                        "stack": [self.snapshot_value(v) for v in self.stack],
                        "output_size": len(self.output),
                    }
                )
            return self.state() if snapshot else None
        except PixelError as e:
            if not e.operation:
                e.operation = str(arg) if op == "BUILTIN" else op
            if e.phase == "data":
                handler_frame = next(
                    (f for f in reversed(self.call_stack) if f.handlers), None
                )
                if handler_frame is not None:
                    while self.call_stack[-1] is not handler_frame:
                        self.call_stack.pop()
                    target, depth = handler_frame.handlers.pop()
                    del self.stack[depth:]
                    handler_frame.pc = target
                    try:
                        error_value = self.allocate("Error", e.fields(), span)
                    except PixelError as fatal:
                        self.halted, self.error = True, fatal.to_dict()
                        raise
                    self.stack.append(error_value)
                    if self.record_trace:
                        self.trace.append(
                            {
                                "step": self.steps,
                                "function": frame.function,
                                "pc": old_pc,
                                "op": ins["op"],
                                "span": span,
                                "stack": [self.snapshot_value(v) for v in self.stack],
                                "output_size": len(self.output),
                                "caught": e.to_dict(),
                            }
                        )
                    return self.state() if snapshot else None
            self.halted = True
            self.error = e.to_dict()
            raise

    def run(self):
        while not self.halted:
            self.step(snapshot=False)
        return deepcopy(self.output)
