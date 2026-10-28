"""A bounded stack VM. Trace/state are independent of source and executable."""
from .value_vector import ValueVector
from .vector_budget import VectorBudget
from .value_map import ValueMap


import math
import re
import struct
from . import unicode_data
import zlib
from copy import deepcopy
from dataclasses import dataclass, field
from functools import lru_cache

from . import jsonvalue, value_boundary, native_events, equality, map_keys, checked_operations
from . import spatial_values as spatial
from .fileaccess import FileAccess
from .code_image import CodeImage
from .flow_metadata import validate_flows
from .native_flow import FlowHandle, FlowRuntime
from .value_access import Access, ValueAccesses
from .jsonvalue import JsonValue
from .model import PixelError
from .typeexpr import is_assignable, is_parameter_type, parse_type, map_parts, is_typed_map, array_item
from .typesys import (
    RUNTIME_BUILTINS,
    BUILTIN_ARITIES,
    ERROR_FIELDS,
    base_type,
    builtin_type,
    is_record,
    valid_type,
)

DATA_OPS = {"MAP", "ITER_SNAPSHOT", "RECORD", "FIELD", "FIELD_SET", "VALUE_SET", "VALUE_CHECK"}
DATA_OPS |= {"ENUM", "ENUM_IS", "ENUM_GET", "PROPAGATE"}
DATA_OPS |= {"VALUE_COLLECTION"}
COLLECTION_OPS = {"ARRAY", "INDEX", "INDEX_SET", "BUILTIN", "CONCAT"}
OPS = {
    "FLOW_NEW", "FLOW_STEP", "FLOW_CONFIG", "FLOW_READ", "FLOW_STAGE", "FLOW_CHILD_STEP",
    "SPATIAL_VALUE",
    "SPATIAL_TRANSFORM",
    "SPATIAL_BINARY",
    "SPACE_NEW",
    "SPACE_OP",
    "RESULT_BUILTIN", "RESULT_DECODE",
    "TRAP",
    "PUSH",
    "LOAD",
    "STORE",
    "BIND",
    "CAPTURE_VALUE",
    "ACCESS",
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
    "STACK_DEPTH",
    "UNWIND",
    "JUMP",
    "JUMP_IF_FALSE",
    "JUMP_IF_TRUE",
}
LIMIT = 2**63
SCALAR_TYPES = {int: "int", float: "float64", bool: "bool", str: "str"}
SCALAR_CLASSES = {name: cls for cls, name in SCALAR_TYPES.items()}
# These containers cannot contain heap references. Avoid copying/scanning PNG
# channel arrays and text tables during every garbage collection.
LEAF_CONTAINERS = frozenset(typ + suffix for typ in ("int", "float64", "bool", "str", "json") for suffix in ("[]", "{}"))
# Typed scalar maps cannot hold heap references in either keys or values.
# Keep enum/spatial/record keys and reference-bearing values on the tracing path.
LEAF_CONTAINERS |= frozenset('map<' + key + ',' + value + '>'
    for key in ('int', 'float64', 'bool', 'str')
    for value in ('int', 'float64', 'bool', 'str', 'json'))


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
        or bc.get("vm_version") != "0.9"
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

    coordinate_frames = bc.get('coordinateFrames', {})
    if (not isinstance(coordinate_frames, dict) or len(coordinate_frames) > 65536
            or any(not isinstance(name, str) or not re.fullmatch(r'@[0-9]{1,5}:[0-9]{1,5}', name)
                   or not isinstance(unit, str) or unit not in spatial.UNITS
                   or name in records or name in enums
                   for name, unit in coordinate_frames.items())):
        fail('Invalid coordinate frame declarations')

    def known_type(value):
        if value.kind == 'spatial':
            if value.name == 'Transform3':
                return len(value.args) == 2 and all(arg.spelling() in coordinate_frames for arg in value.args)
            if value.name in ('Point3', 'Vec3'):
                return len(value.args) == 1 and value.args[0].spelling() in coordinate_frames
            if value.name in ('Space', 'Entity', 'Link'):
                return (len(value.args) == (2 if value.name == 'Entity' else 3)
                        and value.args[0].spelling() in coordinate_frames
                        and all(known_type(arg) for arg in value.args[1:]))
            return False
        if value.kind == 'nominal' and value.spelling() not in records and value.spelling() not in enums:
            return False
        return all(known_type(arg) for arg in value.args)

    def executable_type(t):
        return valid_type(t) and known_type(parse_type(t))

    def executable_parameter(t):
        try:
            parsed = parse_type(t)
            if parsed.kind == 'inout':
                return is_parameter_type(parsed) and executable_type(parsed.args[0].spelling())
            return executable_type(t)
        except (ValueError, TypeError):
            return False

    for typ, schema in records.items():
        if isinstance(typ, str) and typ.startswith("Result<"):
            fail("Result must have its native enum schema")
        if (
            not is_record(typ)
            or not valid_type(typ)
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
        if value.name == "Result" and variants != {
            "Ok": {"value": value.args[0].spelling()},
            "Err": {"error": value.args[1].spelling()},
        }:
            fail("Invalid Result schema")

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
    if not isinstance(bc.get('flows', {}), dict):
        fail('Invalid flow declarations')
    if 'componentFunctions' in bc:
        fail('Legacy component metadata is unsupported')
    effects = bc.get('effectFunctions')
    if (not isinstance(effects, list)
            or any(not isinstance(name, str) or name not in functions for name in effects)
            or len(effects) != len(set(effects))):
        fail('Invalid or missing effect function identities')
    if any(name in bc.get('flows', {}) for name in effects):
        fail('Flow declarations must be pure')
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
            or any(not executable_parameter(t) for t in params)
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
            if op == "CAPTURE_VALUE" and isinstance(arg, dict):
                if set(arg) != {"slot", "type"} or not executable_type(arg["type"]):
                    fail("Invalid capture binding type")
                arg = arg["slot"]
            if (
                op in ("PUSH", "LOAD", "STORE", "BIND", "CAPTURE_VALUE", "UNWIND")
                or op.startswith("JUMP")
            ):
                bound = (
                    len(constants)
                    if op == "PUSH"
                    else loc
                    if op in ("LOAD", "STORE", "BIND", "CAPTURE_VALUE", "UNWIND")
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
            elif op == 'FLOW_NEW' and (not isinstance(arg, str) or arg not in bc.get('flows', {})):
                fail('Unknown flow declaration')
            elif op == 'FLOW_STEP' and (not executable_type(arg) or parse_type(arg).kind != 'flow_handle'):
                fail('Invalid flow instance signature')
            elif op == 'FLOW_CHILD_STEP' and (not isinstance(arg, dict) or set(arg) != {'node', 'reference'}
                    or any(type(value) is not int or value < 0 for value in arg.values())):
                fail('Invalid child flow identity')
            elif op in ('FLOW_CONFIG', 'FLOW_READ', 'FLOW_STAGE') and (type(arg) is not int or arg < 0):
                fail("Invalid flow state cell")
            elif op == "INVOKE" and (
                not executable_type(arg) or parse_type(arg).kind != "function"
            ):
                fail("Invalid indirect call signature")
            elif op == "PROPAGATE":
                if (not isinstance(arg, dict) or set(arg) != {"source", "target"}
                        or any(not isinstance(t, str) or t not in enums for t in arg.values())):
                    fail("Invalid propagation types")
                source, target = parse_type(arg["source"]), parse_type(arg["target"])
                if (source.name not in ("Result", "Option") or target.name != source.name
                        or arg["target"] != f["result"] or name in bc.get("flows", {})):
                    fail("Propagation requires the enclosing function's native Result or Option")
                if source.name == "Result" and source.args[1] != target.args[1]:
                    fail("Propagation error types must match")
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
            elif op == 'SPATIAL_BINARY':
                if not isinstance(arg, str) or arg not in ('+', '-', '*', '==', '!='):
                    fail('Invalid spatial binary operation')
            elif op == 'SPACE_NEW':
                if not executable_type(arg) or parse_type(arg).name != 'Space':
                    fail('Invalid space constructor type')
            elif op == 'SPACE_OP':
                if not isinstance(arg, str) or arg not in spatial.OPERATIONS:
                    fail('Invalid space operation')
            elif op == 'SPATIAL_TRANSFORM':
                if (not isinstance(arg, dict) or not executable_type(arg.get('type'))
                        or parse_type(arg['type']).name != 'Transform3'
                        or not isinstance(arg.get('fields'), list) or len(arg['fields']) != 2
                        or any(not isinstance(f, str) for f in arg['fields'])
                        or set(arg['fields']) != {'rotation', 'translation'}):
                    fail('Invalid spatial transform constructor')
            elif op == 'SPATIAL_VALUE':
                if (not isinstance(arg, dict) or not executable_type(arg.get('type'))
                        or parse_type(arg['type']).kind != 'spatial'
                        or parse_type(arg['type']).name not in ('Point3', 'Vec3')
                        or not isinstance(arg.get('fields'), list)
                        or len(arg['fields']) != 3
                        or any(not isinstance(f, str) for f in arg['fields'])
                        or set(arg['fields']) != {'x', 'y', 'z'}):
                    fail('Invalid spatial value constructor')
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
                    or (map_parts(arg["type"]) is None if op == "MAP" else array_item(arg["type"]) is None)
                    or type(arg.get("count")) is not int
                    or not 0 <= arg["count"] <= 1_000_000
                    or op == "MAP"
                    and arg["count"] % 2 != 0
                ):
                    fail("Invalid array operand")
            elif op == "ACCESS":
                if (not isinstance(arg, dict) or set(arg) != {'slot', 'type', 'path'}
                        or type(arg['slot']) is not int or not 0 <= arg['slot'] < loc
                        or not executable_type(arg['type'])
                        or not isinstance(arg['path'], list) or len(arg['path']) > 128
                        or any(not isinstance(part, dict) or not (
                            set(part) == {'field'} and isinstance(part['field'], str) and part['field']
                            or set(part) == {'index'} and part['index'] is True
                        ) for part in arg['path'])):
                    fail('Invalid inout access operand')
            elif op == "VALUE_COLLECTION":
                if (not isinstance(arg, dict) or set(arg) != {'method', 'result'}
                        or arg['method'] not in ('append', 'extend', 'pop', 'remove')
                        or (arg['result'] != 'unit' if arg['method'] in ('append', 'extend')
                            else not executable_type(arg['result']) or parse_type(arg['result']).name != 'Option')):
                    fail('Invalid collection value operation')
            elif op in ("VALUE_SET", "VALUE_CHECK"):
                if (not isinstance(arg, list) or not 1 <= len(arg) <= 128
                        or any(not isinstance(item, dict) or not (
                            set(item) == {'field'} and isinstance(item['field'], str) and item['field']
                            or set(item) == {'index'} and item['index'] is True
                        ) for item in arg)):
                    fail('Invalid value update path')
            elif op in ("RESULT_BUILTIN", "RESULT_DECODE"):
                if (not isinstance(arg, dict) or set(arg) != ({'name', 'result'} if op == 'RESULT_BUILTIN' else {'result'})
                        or not isinstance(arg.get('result'), str) or arg['result'] not in enums):
                    fail('Invalid checked operation result')
                result = parse_type(arg['result'])
                if result.name != 'Result' or result.args[1].spelling() != 'Error':
                    fail('Checked operations require native Result with Error')
                if op == 'RESULT_BUILTIN':
                    if (not isinstance(arg['name'], str) or arg['name'] not in checked_operations.RESULT_TYPES
                            or result.args[0].spelling() != checked_operations.RESULT_TYPES[arg['name']]):
                        fail('Invalid checked builtin signature')
                elif base_type(result.args[0].spelling()) == 'Error':
                    fail('Invalid JSON decode target')
            elif op == "BUILTIN" and (arg not in RUNTIME_BUILTINS or arg in {"jsonDecode", "fail", *checked_operations.RESULT_TYPES}):
                fail("Unknown builtin")
    if any(
        functions[n]["params"]
        or functions[n].get("captures")
        or functions[n]["result"] != "unit"
        for n in entry
    ):
        fail("Entry functions must have no parameters and return unit")
    validate_flows(bc, fail, executable_type)
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
    flow_root: int | None = None
    flow_path: tuple = ()
    flow_role: str = ''
    flow_cell: int | None = None
    protected: tuple = ()
    pure: bool = False
    return_to_host: bool = False


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
        arguments=None,
    ):
        self.code_image = bytecode if isinstance(bytecode, CodeImage) else CodeImage(bytecode)
        self.bytecode = self.code_image.data
        self.instructions = self.code_image.instructions
        if type(max_steps) is not int or max_steps < 1:
            raise PixelError("runtime", "Step budget must be positive")
        if type(max_depth) is not int or max_depth < 1:
            raise PixelError("runtime", "Call depth budget must be positive")
        for name, limit in (("Heap item", max_heap_items), ("Heap object", max_heap_objects), ("Output", max_output_chars)):
            if type(limit) is not int or limit < 1:
                raise PixelError("runtime", f"{name} budget must be positive")
        self.max_heap_items = max_heap_items
        self.spatial_items = 0
        self.space_identities = set()
        self.flow_runtime = FlowRuntime()
        self.event_binding = None
        self.event_waiting = False
        self.max_heap_objects = max_heap_objects
        self.max_steps, self.max_depth, self.record_trace = max_steps, max_depth, trace
        if not isinstance(input_text, str) or len(input_text) > 1_000_000:
            raise PixelError(
                "runtime", "Input must be a string of at most one million characters"
            )
        self.input_text = input_text
        arguments = [] if arguments is None else arguments
        if (not isinstance(arguments, list) or len(arguments) > 4096
                or any(type(value) is not str for value in arguments)
                or sum(len(value) for value in arguments) > 1_000_000):
            raise PixelError("runtime", "Arguments must be a list of at most 4096 strings and one million characters")
        self.arguments = tuple(arguments)
        self.file_access = file_access if file_access is not None else FileAccess()
        self.effect_cursor = 0
        self.heap_items = 0
        self.vector_budget = VectorBudget()
        self.readonly_counts = {}
        self.value_accesses = ValueAccesses()
        self.equality_types = {}
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

    def _call(self, name, args, *, flow_root=None, flow_path=(), flow_role='', flow_cell=None):
        if len(self.call_stack) >= self.max_depth:
            self.fail("Call stack limit exceeded")
        fn = self.bytecode["functions"][name]
        parent = self.call_stack[-1] if self.call_stack else None
        if flow_root is None and parent is not None:
            flow_root, flow_path = parent.flow_root, parent.flow_path
        graph = self.bytecode.get('flows', {}).get(name)
        if graph is not None and graph.get('stateful') and flow_root is None:
            self.fail_data('Stateful flow requires an explicit instance', code='flow.instance')
        protected = value_boundary.protect(self, args) if graph is not None or flow_role == 'init' else ()
        effectful = name in self.code_image.effect_functions
        if effectful and (parent and parent.pure or flow_root is not None or flow_role == 'init'):
            self.fail('Pure function cannot call an effect fn')
        pure = not effectful
        self.call_stack.append(
            Frame(name, 0, args + [None] * (fn["locals"] - len(args)), len(self.stack),
                  flow_root=flow_root, flow_path=flow_path, flow_role=flow_role, flow_cell=flow_cell,
                  protected=protected, pure=pure)
        )

        if graph is not None and flow_root is not None and flow_role != 'init' and (graph.get('stateful') or flow_role in ('step', 'child')):
            self.flow_runtime.enter(self, self.call_stack[-1])

        for position, value in enumerate(args):
            if isinstance(value, Access):
                if position >= len(fn['params']):
                    self.fail('inout access cannot be captured')
                self.typed(value, fn['params'][position], None)
                self.value_accesses.claim(self, value, self.call_stack[-1])

    def require_mutable(self, value, span):
        if isinstance(value, Ref) and self.heap[value.address].get("readonly_capture", False):
            self.fail("Captured values are readonly", span)
        if isinstance(value, Ref) and value.address in self.readonly_counts:
            self.fail_data('Flow inputs are read-only', span, code='flow.input_mutation')
        if isinstance(value, Ref) and self.heap[value.address].get('immutable', False):
            self.fail_data('Value storage is immutable', span, code='value.immutable')
        if isinstance(value, Ref) and not self.is_cell(value):
            self.fail_data('Value type is immutable', span, code='value.immutable')

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
        # Heap values and exact scalar classes dominate ordinary execution.
        # Resolve them before the less frequent native spatial/runtime values.
        if isinstance(value, Ref):
            return self.heap[value.address]["type"]
        scalar = SCALAR_TYPES.get(type(value))
        if scalar is not None:
            return scalar
        if isinstance(value, FlowHandle):
            return value.type_name
        if isinstance(value, spatial.Transform):
            return f'Transform3<{value.source.identity},{value.target.identity}>'
        if isinstance(value, spatial.SpaceValue):
            return f'Space<{value.frame.identity},{value.payload_type},{value.link_type}>'
        if isinstance(value, spatial.LinkValue):
            return f'Link<{value.frame.identity},{value.entity_type},{value.payload_type}>'
        if isinstance(value, spatial.EntityValue):
            return f'Entity<{value.position.frame.identity},{value.payload_type}>'
        if isinstance(value, spatial.SpatialValue):
            return f'{value.kind}<{value.frame.identity}>'
        if isinstance(value, JsonValue):
            return "json"
        return "uninitialized"

    def typed(self, value, typ, span):
        if isinstance(value, Access):
            expected = "inout<" + value.type_name + ">"
            if typ != expected:
                self.fail(f"Expected runtime {typ}, got an inout access", span)
            self.value_accesses.read(self, value, span)
            return value
        # Exact classes preserve the distinction between bool and int.
        if type(value) is SCALAR_CLASSES.get(typ):
            return value
        actual = self.value_type(value)
        if actual != typ:
            # Binding cells are mutable storage, not callable values. Their
            # declared type must never be widened through a closure alias.
            if (not isinstance(value, Ref) or self.is_cell(value)
                    or not is_assignable(parse_type(typ), parse_type(actual))):
                self.fail(f"Expected runtime {typ}", span)
        return value

    def callable_type(self, name):
        target = self.bytecode["functions"][name]
        prefix = "effect_fn" if name in self.code_image.effect_functions else "fn"
        return prefix + "(" + ",".join(target["params"]) + ")->(" + target["result"] + ")"

    def snapshot_value(self, value):
        if isinstance(value, Access):
            entry = self.value_accesses.entries.get(value.identity)
            if entry is None or self.cancelled:
                return {"type": value.type_name, "unavailable": True}
            return self.snapshot_value(self.value_accesses._read(self, entry))
        if isinstance(value, FlowHandle):
            instance = self.flow_runtime.instances.get(value.identity)
            return {'type': value.type_name, 'instance': value.identity, 'function': value.target,
                    'step': instance.steps if instance is not None else None}
        if isinstance(value, spatial.Transform):
            return {'type': self.value_type(value), 'rotation': [list(row) for row in value.rotation],
                    'translation': list(value.translation)}
        if isinstance(value, spatial.SpaceValue):
            return {'type': self.value_type(value), 'identity': value.identity,
                    'version': value.version, 'entities': len(value.entities), 'links': len(value.links)}
        if isinstance(value, spatial.LinkValue):
            return {'type': self.value_type(value), 'space': value.owner,
                    'identity': value.identity, 'source': value.source, 'target': value.target}
        if isinstance(value, spatial.EntityValue):
            return {'type': self.value_type(value), 'space': value.owner,
                    'identity': value.identity, 'position': list(value.position.xyz)}
        if isinstance(value, spatial.SpatialValue):
            return {'type': self.value_type(value), 'unit': value.frame.unit,
                    'coordinates': list(value.xyz)}
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
        if isinstance(value, Access):
            self.fail("inout access cannot be serialized as a value", span)
        if isinstance(value, FlowHandle):
            self.fail_data('Flow instances cannot be serialized as ordinary values', span, code='flow.serialization')
        if isinstance(value, spatial.Transform):
            return {'rotation': [[self.materialize(n, span, ancestors) for n in row] for row in value.rotation],
                    'translation': self.materialize(spatial.SpatialValue('Vec3', value.target, value.translation), span, ancestors)}
        if isinstance(value, spatial.SpaceValue):
            return {'identity': self.materialize(value.identity, span, ancestors),
                    'version': self.materialize(value.version, span, ancestors),
                    'entities': [self.materialize(e, span, ancestors) for e in value.entities],
                    'links': [self.materialize(e, span, ancestors) for e in value.links]}
        if isinstance(value, spatial.LinkValue):
            return {'identity': self.materialize(value.identity, span, ancestors),
                    'source': self.materialize(value.source, span, ancestors),
                    'target': self.materialize(value.target, span, ancestors),
                    'payload': self.materialize(value.payload, span, ancestors)}
        if isinstance(value, spatial.EntityValue):
            return {'identity': self.materialize(value.identity, span, ancestors),
                    'position': self.materialize(value.position, span, ancestors),
                    'payload': self.materialize(value.payload, span, ancestors)}
        if isinstance(value, spatial.FrozenValue):
            if is_typed_map(value.type_name):
                key_type, _ = map_parts(value.type_name)
                if key_type == 'str':
                    return {self.materialize(k, span, ancestors): self.materialize(v, span, ancestors)
                            for k, v in value.items}
                return [dict(key=self.materialize(k, span, ancestors),
                             value=self.materialize(v, span, ancestors)) for k, v in value.items]
            if value.type_name in self.bytecode['enums']:
                fields = dict(value.items)
                return {'variant': fields['$variant'], 'fields': {
                    k: self.materialize(v, span, ancestors) for k, v in fields.items() if k != '$variant'}}
            if is_record(value.type_name):
                return {self.materialize(k, span, ancestors): self.materialize(v, span, ancestors)
                        for k, v in value.items}
            return [self.materialize(v, span, ancestors) for v in value.items]
        if isinstance(value, spatial.SpatialValue):
            return {axis: self.materialize(component, span, ancestors)
                    for axis, component in zip(('x', 'y', 'z'), value.xyz)}
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
                if isinstance(items, (dict, ValueMap)):
                    if is_typed_map(self.value_type(value)):
                        key_type, _ = map_parts(self.value_type(value))
                        if key_type == 'str':
                            return {self.materialize(k.value, span, ancestors): self.materialize(v, span, ancestors)
                                    for k, v in items.items()}
                        return [dict(key=self.materialize(k.value, span, ancestors),
                                     value=self.materialize(v, span, ancestors)) for k, v in items.items()]
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

    def reserve_spatial(self, count, span):
        # Charge native immutable allocations separately from mutable heap cells.
        # Applications rebase this counter to live state at event boundaries;
        # allocations within an event remain bounded, including temporary values.
        if self.spatial_items + count > self.max_heap_items:
            self.fail_data('Spatial allocation budget exceeded', span, code='spatial.budget')
        self.spatial_items += count

    def recount_spatial(self):
        """Rebase native allocation accounting at an idle application boundary.

        An event still has a bounded allocation budget. Between events only live
        VM roots count; event outputs, queues and recordings have independent
        host budgets. This is accounting, not mutation of native values.
        """
        if self.flow_runtime.transaction is not None or not self.event_waiting:
            self.fail('Native allocation recount requires an idle event boundary', None)
        pending = list(self.stack)
        for frame in self.call_stack:
            pending.extend(frame.memory)
        count = len(self.space_identities) + len(self.flow_runtime.instances)
        for instance in self.flow_runtime.instances.values():
            count += len(instance.cells)
            pending.extend(instance.cells.values())
            for values in instance.configuration.values():
                count += len(values)
                pending.extend(values)
        if self.event_binding is not None:
            pending.append(self.event_binding.tick_event)
        references, native = set(), set()
        while pending:
            value = pending.pop()
            if isinstance(value, Ref):
                if value.address in references:
                    continue
                references.add(value.address)
                obj = self.heap[value.address]
                if not obj.get('cell') and obj['type'] in LEAF_CONTAINERS:
                    continue
                items = obj['items']
                pending.extend(items.values() if isinstance(items, (dict, ValueMap)) else items)
                if isinstance(items, (dict, ValueMap)):
                    pending.extend(key.value for key in items if isinstance(key, map_keys.Key))
                continue
            if not isinstance(value, (spatial.SpatialValue, spatial.Transform, spatial.FrozenValue,
                                      spatial.SpaceValue, spatial.EntityValue, spatial.LinkValue)):
                continue
            identity = id(value)
            if identity in native:
                continue
            native.add(identity)
            if isinstance(value, (spatial.SpatialValue, spatial.Transform)):
                count += 12
            elif isinstance(value, spatial.FrozenValue):
                count += 1 + len(value.items)
                if is_typed_map(value.type_name):
                    pending.extend(item for pair in value.items for item in pair)
                elif is_record(value.type_name):
                    pending.extend(item for _, item in value.items)
                else:
                    pending.extend(value.items)
            elif isinstance(value, spatial.SpaceValue):
                count += 1 + len(value.entities) + len(value.links)
                pending.extend(value.entities)
                pending.extend(value.links)
            elif isinstance(value, spatial.EntityValue):
                count += 1
                pending.extend((value.position, value.payload))
            else:
                count += 1
                pending.append(value.payload)
            if count > self.max_heap_items:
                self.fail_data('Live spatial state exceeds the allocation budget', None, code='spatial.budget')
        if count > self.max_heap_items:
            self.fail_data('Live spatial state exceeds the allocation budget', None, code='spatial.budget')
        self.spatial_items = count

    def freeze_spatial_payload(self, value, span, ancestors=None):
        ancestors = set() if ancestors is None else ancestors
        self.reserve_spatial(1, span)
        if isinstance(value, FlowHandle):
            self.fail_data('A flow handle cannot be retained in immutable state or spatial payloads', span,
                           code='flow.payload')
        if not isinstance(value, Ref):
            return value
        if value.address in ancestors or len(ancestors) >= 128:
            self.fail_data('Spatial payload must be acyclic and within depth 128', span,
                           code='spatial.payload_cycle')
        typ = self.value_type(value)
        if parse_type(typ).kind == 'function' or self.is_cell(value):
            self.fail_data('Spatial payload cannot capture executable state', span,
                           code='spatial.payload_function')
        ancestors.add(value.address)
        try:
            items = self.heap[value.address]['items']
            if is_typed_map(typ):
                frozen = tuple((self.freeze_spatial_payload(k.value, span, ancestors),
                                self.freeze_spatial_payload(v, span, ancestors)) for k, v in items.items())
            elif isinstance(items, (dict, ValueMap)):
                frozen = tuple((k, self.freeze_spatial_payload(v, span, ancestors)) for k, v in items.items())
            else:
                frozen = tuple(self.freeze_spatial_payload(v, span, ancestors) for v in items)
            return spatial.FrozenValue(typ, frozen)
        finally:
            ancestors.remove(value.address)

    def thaw_spatial_payload(self, value, span):
        if not isinstance(value, spatial.FrozenValue):
            return value
        mapped = is_record(value.type_name)
        # Child allocations must remain roots during subsequent allocations.
        base = len(self.stack)
        try:
            if is_typed_map(value.type_name):
                entries = []
                for key, child in value.items:
                    key = self.thaw_spatial_payload(key, span)
                    self.stack.append(key)
                    child = self.thaw_spatial_payload(child, span)
                    self.stack.append(child)
                    entries.append((key, child))
                return self.allocate(value.type_name, entries, span)
            items = {} if mapped else []
            for pair in value.items:
                child = self.thaw_spatial_payload(pair[1] if mapped else pair, span)
                self.stack.append(child)
                if mapped: items[pair[0]] = child
                else: items.append(child)
            return self.allocate(value.type_name, items, span)
        finally:
            del self.stack[base:]

    def space_operation(self, name, args, span, *, origin=None):
        first = args[0]
        if name in ('transformPoint', 'transformVector', 'transformThen'):
            if not isinstance(first, spatial.Transform): raise spatial.SpatialError('Expected a coordinate transform')
            if name == 'transformThen': return first.then(args[1])
            kind = 'Point3' if name == 'transformPoint' else 'Vec3'
            if not isinstance(args[1], spatial.SpatialValue) or args[1].kind != kind:
                raise spatial.SpatialError('Incorrect transform operand type')
            return first.apply(args[1], origin=origin)
        if name == 'distance3': return spatial.distance(*args)
        if name == 'dot3': return spatial.dot(*args)
        if name in ('linkId', 'linkPayload'):
            if not isinstance(first, spatial.LinkValue):
                raise spatial.SpatialError('Expected a link value')
            if name == 'linkId': return first.identity
            return self.thaw_spatial_payload(first.payload, span)
        if name.startswith('entity'):
            if not isinstance(first, spatial.EntityValue):
                raise spatial.SpatialError('Expected an entity value')
            if name == 'entityPosition': return first.position
            if name == 'entityId': return first.identity
            return self.thaw_spatial_payload(first.payload, span)
        if not isinstance(first, spatial.SpaceValue):
            raise spatial.SpatialError('Expected a space snapshot')
        if name == 'spaceSize': return len(first.entities)
        if name == 'spaceLinkCount': return len(first.links)
        if name == 'spaceGetLink': return first.get_link(args[1])
        if name == 'linkSource': return first.link_source(args[1])
        if name == 'linkTarget': return first.link_target(args[1])
        if name in ('spaceOutgoing', 'spaceIncoming'):
            links = first.outgoing(args[1]) if name == 'spaceOutgoing' else first.incoming(args[1])
            return self.allocate(f'Link<{first.frame.identity},{first.payload_type},{first.link_type}>[]', list(links), span)
        if name == 'spaceGet': return first.get(args[1])
        if name == 'spaceAhead': return first.ahead(*args[1:])
        if name == 'spaceNeighbors':
            neighbors = first.neighbors(*args[1:])
            return self.allocate(f'Entity<{first.frame.identity},{first.payload_type}>[]', list(neighbors), span)
        self.reserve_spatial(len(first.entities) + len(first.links) + 1, span)
        if name == 'spaceConnect':
            self.typed(args[3], first.link_type, span)
            return first.connect(args[1], args[2], self.freeze_spatial_payload(args[3], span))
        if name == 'spaceDisconnect': return first.disconnect(args[1])
        if name == 'spaceSetLinkPayload':
            self.typed(args[2], first.link_type, span)
            return first.set_link_payload(args[1], self.freeze_spatial_payload(args[2], span))
        if name == 'spaceInsert':
            self.typed(args[2], first.payload_type, span)
            payload = self.freeze_spatial_payload(args[2], span)
            return first.insert(args[1], payload)
        if name == 'spaceMove': return first.move(*args[1:])
        if name == 'spaceRemove': return first.remove(args[1])
        raise spatial.SpatialError('Unknown space operation')

    def allocate(self, typ, items, span, *, cell=False):
        if not valid_type(typ):
            self.fail('Invalid allocation type', span)
        underlying = parse_type(typ)
        immutable = not cell and underlying.kind != 'function'
        explicit_map = is_typed_map(typ)
        if explicit_map and not cell:
            key_type, value_type = map_parts(typ)
            if not map_keys.supports(self, key_type):
                self.fail('Type does not support map keys: ' + key_type, span)
            normalized = {}
            for key, value in items.items() if isinstance(items, (dict, ValueMap)) else items:
                key = map_keys.wrap(self, key.value if isinstance(key, map_keys.Key) else key, key_type, span)
                self.typed(value, value_type, span)
                if key in normalized:
                    self.fail_data('Duplicate map key', span, code='collection.duplicate_key')
                normalized[key] = value
            items = normalized
        record = is_record(typ)
        callable_value = parse_type(typ).kind == "function"
        mapped = cell or explicit_map or record or callable_value
        roots = list(items.values()) if mapped else items
        if explicit_map and not cell:
            roots.extend(key.value for key in items)
        if (
            # Scale tracing frequency with the live graph instead of rescanning a
            # compiler-sized heap after every 128 allocations. Hard limits below
            # still force collection before any allocation can exceed its budget.
            self.allocations_since_gc >= max(128, len(self.heap) // 2)
            or self.heap_items + len(items) > self.max_heap_items
            or len(self.heap) >= self.max_heap_objects
        ):
            self.collect(() if typ in LEAF_CONTAINERS and not cell else roots)
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
                != self.callable_type(items["$function"])
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
                if not self.heap[captured.address].get("readonly_capture", False):
                    self.fail("Closure captures must be readonly values", span)
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
        elif explicit_map:
            pass  # Keys and values were checked before allocation or collection.
        elif not (array_item(typ) == "int" and type(items) is bytearray):
            for item in roots:
                self.typed(item, array_item(typ) or typ[:-2], span)
        if immutable:
            for item in roots:
                if isinstance(item, Ref) and not self.is_cell(item) and parse_type(self.value_type(item)).kind != 'function' and not self.heap[item.address].get('immutable', False):
                    self.fail('Readonly allocation requires immutable children', span)
        if array_item(typ) == "int" and not cell and (type(items) is bytearray or len(items) >= 4096):
            try:
                storage = bytearray(items)
            except (ValueError, OverflowError):
                storage = list(items)
        else:
            storage = dict(items) if mapped else list(items)
        address = self.next_address
        self.next_address += 1
        self.allocations_since_gc += 1
        self.heap[address] = {
            "type": typ,
            "items": storage,
            "immutable": immutable,
            **({"cell": True} if cell else {}),
        }
        self.heap_items += len(items)
        return Ref(address)

    def is_cell(self, value):
        return isinstance(value, Ref) and self.heap[value.address].get("cell", False)

    def binding_value(self, value):
        if isinstance(value, Access):
            return self.value_accesses.read(self, value)
        return (
            self.heap[value.address]["items"]["value"] if self.is_cell(value) else value
        )

    def capture_value(self, frame, slot, span=None, *, typ=None):
        """Capture the current value in a fresh, readonly binding cell."""
        value = frame.memory[slot]
        if value is None:
            self.fail("Capture of uninitialized binding", span)
        if isinstance(value, Access):
            self.fail("inout parameters cannot be captured", span)
        value = self.binding_value(value)
        actual = self.value_type(value)
        if typ is not None:
            self.typed(value, typ, span)
        captured = self.allocate(typ or actual, {"value": value}, span, cell=True)
        self.heap[captured.address]["readonly_capture"] = True
        return captured

    def make_enum(self, typ, tag, fields, values, span):
        schema = self.bytecode["enums"].get(typ, {}).get(tag)
        if schema is None or set(fields) != set(schema) or len(fields) != len(values):
            self.fail("Invalid enum construction", span)
        for field_name, value in zip(fields, values):
            self.typed(value, schema[field_name], span)
        return self.allocate(typ, {"$variant": tag, **dict(zip(fields, values))}, span)

    def collect(self, extra_roots=()):
        """Trace references from all live frames, operands and transient allocation roots."""
        pending = list(self.stack) + list(extra_roots) + list(self.value_accesses.roots())
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
                pending.extend(items.values() if isinstance(items, (dict, ValueMap)) else items)
                if isinstance(items, (dict, ValueMap)):
                    pending.extend(key.value for key in items if isinstance(key, map_keys.Key))
        removed = set(self.heap) - reachable
        for address in removed:
            items = self.heap.pop(address)["items"]
            self.heap_items -= (self.vector_budget.release(items)
                                if isinstance(items, ValueVector) else len(items))
            self.readonly_counts.pop(address, None)
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
            "readonly_counts",
            "value_accesses",
            "spatial_items",
            "space_identities",
            "flow_runtime",
            "event_binding",
            "event_waiting",
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
        # The trusted snapshot preserves shared tuples; rebuild identity keys
        # for its copied storage rather than retaining the abandoned heap.
        self.vector_budget = VectorBudget()
        for obj in self.heap.values():
            if isinstance(obj['items'], ValueVector):
                self.vector_budget.retain(obj['items'])
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
                if parse_type(typ).kind == 'flow_handle':
                    self.fail_data('Flow instances must be explicitly created', span, code='json.unsupported_type')
                if typ == "json":
                    return item
                if typ == "int":
                    return jsonvalue.as_int(item)
                if typ == "float64":
                    return jsonvalue.as_float64(item)
                if typ in ("str", "bool"):
                    return jsonvalue.require(item, "string" if typ == "str" else "bool")
                if array_item(typ) is not None:
                    values = jsonvalue.require(item, "array")
                    return allocate(
                        typ,
                        [
                            convert(v, array_item(typ), f"{path}[{i}]", depth + 1)
                            for i, v in enumerate(values)
                        ],
                    )
                if typ in self.bytecode["enums"] and parse_type(typ).name == "Option":
                    element = parse_type(self.bytecode['enums'][typ]['Some']['value'])
                    if parse_type(element.spelling()).name == "Option":
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
                if is_typed_map(typ):
                    key_type, value_type = map_parts(typ)
                    if key_type == 'str':
                        entries = [(key, convert(child, value_type,
                                    jsonvalue.field_path(path, key), depth + 1))
                                   for key, child in jsonvalue.require(item, 'object')]
                    else:
                        entries = []
                        for i, entry in enumerate(jsonvalue.require(item, 'array')):
                            visited += 1  # Include the entry object in the structure budget.
                            entry_path = f'{path}[{i}]'
                            fields = dict(jsonvalue.require(entry, 'object'))
                            if set(fields) != {'key', 'value'}:
                                self.fail_data('Map entry requires exactly key and value', span,
                                               code='json.type_mismatch', path=entry_path)
                            entries.append((
                                convert(fields['key'], key_type, entry_path + '.key', depth + 2),
                                convert(fields['value'], value_type, entry_path + '.value', depth + 2)))
                    return allocate(typ, entries)
                values = dict(jsonvalue.require(item, "object"))
                parsed = parse_type(typ)
                if parsed.kind == 'spatial' and parsed.name == 'Transform3':
                    if set(values) != {'rotation', 'translation'}:
                        self.fail_data('Transform JSON requires rotation and translation', span,
                                       code='json.spatial_fields', path=path)
                    rows = jsonvalue.require(values['rotation'], 'array')
                    if len(rows) != 3:
                        self.fail_data('Transform rotation requires three rows', span, code='json.spatial_fields', path=path)
                    rotation = []
                    for row in rows:
                        entries = jsonvalue.require(row, 'array')
                        if len(entries) != 3:
                            self.fail_data('Transform rotation requires three columns', span, code='json.spatial_fields', path=path)
                        rotation.append(tuple(convert(v, 'float64', path + '.rotation', depth + 2) for v in entries))
                    source_id, target_id = [a.spelling() for a in parsed.args]
                    table = self.bytecode['coordinateFrames']
                    translation = convert(values['translation'], f'Vec3<{target_id}>', path + '.translation', depth + 1)
                    try:
                        self.reserve_spatial(12, span)
                        return spatial.Transform(spatial.CoordinateFrame(source_id, table[source_id]),
                                                 spatial.CoordinateFrame(target_id, table[target_id]),
                                                 tuple(rotation), translation.xyz)
                    except spatial.SpatialError as error:
                        self.fail_data(str(error), span, code='spatial.invalid_transform', path=path)
                if parsed.kind == 'spatial' and parsed.name in ('Point3', 'Vec3'):
                    if set(values) != {'x', 'y', 'z'}:
                        self.fail_data('Spatial JSON requires exactly x, y and z', span,
                                       code='json.spatial_fields', path=path)
                    identity = parsed.args[0].spelling()
                    frame = spatial.CoordinateFrame(identity, self.bytecode['coordinateFrames'][identity])
                    return spatial.SpatialValue(parsed.name, frame, tuple(
                        convert(values[axis], 'float64', jsonvalue.field_path(path, axis), depth + 1)
                        for axis in ('x', 'y', 'z')))
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
            if isinstance(item, FlowHandle):
                self.fail_data('Flow instances cannot be serialized as ordinary values', span, code='flow.serialization')
            if isinstance(item, spatial.Transform):
                return JsonValue('object', (
                    ('rotation', jsonvalue.from_host([list(row) for row in item.rotation])),
                    ('translation', visit(spatial.SpatialValue('Vec3', item.target, item.translation), depth + 1)),
                ))
            if isinstance(item, (spatial.SpaceValue, spatial.EntityValue, spatial.LinkValue)):
                self.fail_data('Space snapshots require typed snapshot serialization', span,
                               code='json.unsupported_type')
            if isinstance(item, spatial.SpatialValue):
                return JsonValue('object', tuple((axis, visit(value, depth + 1))
                                                 for axis, value in zip(('x', 'y', 'z'), item.xyz)))
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
                        if parse_type(parse_type(typ).args[0].spelling()).name == "Option":
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
                if is_typed_map(typ):
                    key_type, _ = map_parts(typ)
                    if key_type == 'str':
                        return JsonValue('object', tuple(
                            (key.value, visit(child, depth + 1)) for key, child in values.items()))
                    entries = []
                    for key, child in values.items():
                        count += 1  # The entry object is a JSON node too.
                        entries.append(JsonValue('object', (
                            ('key', visit(key.value, depth + 2)),
                            ('value', visit(child, depth + 2)))))
                    return JsonValue('array', tuple(entries))
                if isinstance(values, (dict, ValueMap)):
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

    def decompress(self, value, limit, target, span):
        if target != "int[]":
            self.fail("Invalid decompression target", span)
        self.typed(value, "int[]", span)
        self.typed(limit, "int", span)
        items = self.sequence(value, span)
        if type(items) is not bytearray and any(type(item) is not int or not 0 <= item <= 255 for item in items):
            self.fail_data("Byte values must be integers from 0 to 255", span, code="bytes.range")
        if not 0 <= limit <= self.max_heap_items:
            self.fail_data("Decompression limit must fit the heap item budget", span, code="bytes.limit")
        try:
            decoder = zlib.decompressobj()
            result = decoder.decompress(bytes(items), limit + 1)
        except zlib.error:
            self.fail_data("Invalid zlib stream", span, code="bytes.compression")
        if len(result) > limit:
            self.fail_data("Decompressed data exceeds output limit", span, code="bytes.limit")
        if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            self.fail_data("Truncated or trailing zlib stream", span, code="bytes.compression")
        return self.allocate(target, bytearray(result), span)

    def builtin(self, name, args, span):
        try:
            _builtin_result(name, tuple(self.value_type(v) for v in args))
        except ValueError as e:
            self.fail(str(e), span)
        if name == "programArgs":
            return self.allocate("str[]", self.arguments, span)
        if name in ("readChunk", "listDirectory"):
            cursor = self.effect_cursor
            self.effect_cursor += 1
            try:
                value = self.file_access.perform(name, args[0], args[1:] if name == "readChunk" else None, cursor)
                return jsonvalue.from_host(value)
            except PixelError as error:
                error.span = span
                raise
        if name in ("readBytes", "writeBytes"):
            value = args[1]
            if name == "writeBytes":
                items = self.sequence(value, span)
                value = items if type(items) is bytearray and not self.file_access.record else list(items)
                if type(items) is not bytearray and any(type(byte) is not int or not 0 <= byte <= 255 for byte in value):
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
        if name == "input":
            return self.input_text
        if name == "lookup":
            items = self.heap[args[0].address]["items"]
            source_type = self.value_type(args[0])
            key_type, value_type = map_parts(source_type)
            key = map_keys.wrap(self, args[1], key_type, span) if is_typed_map(source_type) else args[1]
            typ = _builtin_result(name, tuple(self.value_type(v) for v in args))
            if key in items:
                return self.make_enum(typ, "Some", ["value"], [items[key]], span)
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
        if name in ("keys", "has"):
            items = self.heap[value.address]["items"]
            source_type = self.value_type(value)
            key_type, _ = map_parts(source_type)
            explicit = is_typed_map(source_type)
            if name == "keys":
                return self.allocate(key_type + "[]", [key.value for key in items] if explicit else list(items), span)
            key = map_keys.wrap(self, args[1], key_type, span) if explicit else args[1]
            present = key in items
            return present
        if name == "arrayRepeat":
            items = self.sequence(value, span)
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
            typ = self.value_type(value)
            if (array_item(typ) == "int" and size >= 4096 and
                    (type(items) is bytearray or all(0 <= item <= 255 for item in items))):
                return self.allocate(typ, bytearray(items) * count, span)
            return self.allocate(typ, items * count, span)
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
            return self.allocate("int[]", bytearray(encoded), span)
        if name == "float64Bytes":
            return self.allocate("int[]", list(struct.pack(">d", value)), span)
        if name == "zlibDecompress":
            return self.decompress(value, args[1], "int[]", span)
        if name in ("crc32", "zlibCompress"):
            items = self.sequence(value, span)
            if type(items) is not bytearray and any(type(item) is not int or not 0 <= item <= 255 for item in items):
                self.fail_data("Byte values must be integers from 0 to 255", span, code="bytes.range")
            data = bytes(items)
            if name == "crc32":
                return zlib.crc32(data)
            result = zlib.compress(data)
            return self.allocate("int[]", bytearray(result), span)
        if name in ("utf8Decode", "float64FromBytes"):
            items = self.sequence(value, span)
            if type(items) is not bytearray and any(type(item) is not int or not 0 <= item <= 255 for item in items):
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
            return unicode_data.category(value)
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

    def _return_value(self, frame, value, span):
        result = self.bytecode["functions"][frame.function]["result"]
        if result != "unit":
            self.typed(value, result, span)
        if len(self.stack) < frame.base:
            self.fail("Operand stack underflow at return", span)
        # A return or failed propagation releases pending frame operands.
        # Caller operands below this frame's base remain untouched.
        del self.stack[frame.base:]
        if frame.flow_role != 'init':
            value = value_boundary.publish(self, frame, value, span)
        self.call_stack.pop()
        self.value_accesses.release_frame(frame)
        value_boundary.release(self, frame)
        consumed = self.flow_runtime.returned(self, frame, value, span)
        if not consumed:
            if self.call_stack or frame.return_to_host:
                self.stack.append(value)
            else:
                self._start_entry()

    def _propagate(self, frame, arg, span):
        # Keep the source rooted while allocating a differently typed Err/None.
        if len(self.stack) <= frame.base:
            self.fail("Operand stack underflow", span)
        value = self.typed(self.stack[-1], arg["source"], span)
        family = parse_type(arg["source"]).name
        success, failure = ("Ok", "Err") if family == "Result" else ("Some", "None")
        items = self.heap[value.address]["items"]
        if items["$variant"] == success:
            self.stack[-1] = items["value"]
            return
        if items["$variant"] != failure:
            self.fail("Invalid propagation variant", span)
        result = value
        if arg["source"] != arg["target"]:
            fields, values = (["error"], [items["error"]]) if family == "Result" else ([], [])
            result = self.make_enum(arg["target"], failure, fields, values, span)
        # An early return abandons this frame's pending operand computations.
        # Inout accesses are released by the same boundary as a normal return.
        self._return_value(frame, result, span)

    def state(self):
        def export(values, typ=None):
            if typ and is_typed_map(typ):
                return [{"key": self.snapshot_value(k.value), "value": self.snapshot_value(v)}
                        for k, v in values.items()]
            if isinstance(values, (dict, ValueMap)):
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
                    "items": export(v["items"], None if v.get("cell") else v["type"]),
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
        if self.halted or self.event_waiting:
            return self.state()
        frame = self.call_stack[-1]
        instructions = self.instructions[frame.function]
        instruction = instructions[frame.pc] if frame.pc < len(instructions) else None
        op, arg, span, node = instruction if instruction else ("STEP", None, None, None)
        try:
            if self.cancelled:
                self.fail("Execution cancelled", span)
            if self.steps >= self.max_steps:
                self.fail("Instruction budget exceeded", span)
            if instruction is None:
                self.fail("Function fell off instruction stream", span)
            old_pc = frame.pc
            frame.pc += 1
            self.steps += 1
            if (frame.pure or frame.flow_root is not None) and (op == 'PRINT' or op in ('BUILTIN', 'RESULT_BUILTIN') and (arg if op == 'BUILTIN' else arg['name']) in {
                    'input', 'programArgs', 'readText', 'writeText', 'readBytes', 'writeBytes',
                    'readChunk', 'listDirectory'}):
                self.fail('External effects are forbidden inside a pure function', span)
            # State initializers run within the explicit flowNew transaction.
            # Bytecode validation forbids SPACE_NEW in child argument initializers
            # and prevents initializer functions from being called directly.
            initial_space = op == 'SPACE_NEW' and frame.flow_role == 'init'
            if frame.pure and op in ('FLOW_NEW', 'FLOW_STEP', 'SPACE_NEW') and not initial_space:
                self.fail('Runtime identity effects are forbidden inside a pure function', span)
            if op == "PUSH":
                self.stack.append(arg)
            elif op == "LOAD":
                if frame.memory[arg] is None:
                    self.fail("Read of uninitialized memory", span)
                self.stack.append(self.binding_value(frame.memory[arg]))
            elif op == "CAPTURE_VALUE":
                slot = arg["slot"] if isinstance(arg, dict) else arg
                typ = arg["type"] if isinstance(arg, dict) else None
                self.stack.append(self.capture_value(frame, slot, span, typ=typ))
            elif op == "BIND":
                if self.value_accesses.entries:
                    self.value_accesses.check_direct_write(self, frame, arg, span)
                value = self.pop(span)
                if isinstance(value, Access):
                    self.fail("inout access cannot be stored as a value", span)
                frame.memory[arg] = value
            elif op == "STORE":
                value = self.pop(span)
                binding = frame.memory[arg]
                if isinstance(value, Access):
                    self.fail("inout access cannot be stored as a value", span)
                if isinstance(binding, Access):
                    self.value_accesses.write(self, binding, value, span)
                elif self.is_cell(binding):
                    if self.value_accesses.entries:
                        self.value_accesses.check_direct_write(self, frame, arg, span)
                    self.require_mutable(binding, span)
                    self.typed(value, self.value_type(binding), span)
                    if self.is_cell(value):
                        self.fail("Cannot assign a binding cell as a value", span)
                    self.heap[binding.address]["items"]["value"] = value
                else:
                    if self.value_accesses.entries:
                        self.value_accesses.check_direct_write(self, frame, arg, span)
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
            elif op == 'FIELD' and self.stack and isinstance(self.stack[-1], spatial.Transform):
                value = self.pop(span)
                if arg == 'translation':
                    self.stack.append(spatial.SpatialValue('Vec3', value.target, value.translation))
                elif arg == 'rotation':
                    frozen = spatial.FrozenValue('float64[][]', tuple(spatial.FrozenValue('float64[]', row) for row in value.rotation))
                    self.stack.append(self.thaw_spatial_payload(frozen, span))
                else: self.fail('Unknown transform field', span)
            elif op == 'FIELD' and self.stack and isinstance(self.stack[-1], spatial.SpatialValue):
                value = self.pop(span)
                if arg not in ('x', 'y', 'z'):
                    self.fail('Unknown spatial coordinate', span)
                self.stack.append(value.xyz[('x', 'y', 'z').index(arg)])
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
                    self.require_mutable(value, span)
                    if typ == "Error":
                        self.fail("Error fields are read-only", span)
                    self.typed(assigned, self.bytecode["records"][typ][arg], span)
                    items[arg] = assigned
                else:
                    self.stack.append(items[arg])
            elif op == "ACCESS":
                indices = [self.pop(span) for part in arg['path'] if 'index' in part][::-1]
                self.stack.append(self.value_accesses.begin(self, frame, arg['slot'], arg['path'],
                                                           indices, span, typ=arg['type']))
            elif op == "VALUE_COLLECTION":
                from .value_update import edit_collection
                args = [] if arg['method'] == 'pop' else [self.pop(span)]
                base = self.pop(span)
                updated, result = edit_collection(self, base, arg['method'], args, arg['result'], span)
                self.stack.extend([updated, result])
            elif op == "VALUE_CHECK":
                from .value_update import check_path
                indices = [self.pop(span) for item in arg if 'index' in item][::-1]
                check_path(self, self.pop(span), arg, indices, span)
            elif op == "VALUE_SET":
                from .value_update import replace_path
                replacement = self.pop(span)
                indices = [self.pop(span) for item in arg if 'index' in item][::-1]
                base = self.pop(span)
                self.stack.append(replace_path(self, base, arg, indices, replacement, span))
            elif op in ('RESULT_BUILTIN', 'RESULT_DECODE'):
                name = arg['name'] if op == 'RESULT_BUILTIN' else 'jsonDecode'
                arity = 1 if op == 'RESULT_DECODE' else BUILTIN_ARITIES[name]
                if len(self.stack) - frame.base < arity:
                    self.fail('Operand stack underflow', span)
                # Keep arguments rooted until codecs and both result allocations
                # finish, even when a collection is only a temporary expression.
                start = len(self.stack) - arity
                arguments = self.stack[start:]
                target = parse_type(arg['result']).args[0].spelling() if op == 'RESULT_DECODE' else None
                result = checked_operations.execute(self, name, arguments, arg['result'], span, decode_target=target)
                del self.stack[start:]
                self.stack.append(result)
            elif op == "ITER_SNAPSHOT":
                value = self.pop(span)
                items = self.sequence(value, span)
                typ = self.value_type(value)
                if is_record(typ):
                    self.fail("Record values are not iterable", span)
                if is_typed_map(typ):
                    key_type, value_type = map_parts(typ)
                    self.stack.append(value)  # Keep keys alive across both allocations.
                    if arg == 'pair':
                        values_type = value_type + '[]'
                        values = self.allocate(values_type, list(items.values()), span)
                        self.stack.append(values)
                    keys = self.allocate(key_type + '[]', [key.value for key in items], span)
                    if arg == 'pair':
                        self.stack.pop()
                    self.stack.pop()
                    self.stack.append(keys)
                    if arg == 'pair':
                        self.stack.append(values)
                elif arg == "pair":
                    values = list(items)
                    # Root values before allocating keys: a GC may run on either allocation.
                    self.stack.append(
                        self.allocate(
                            typ
                            if array_item(typ) is not None
                            else "str[]",
                            values,
                            span,
                        )
                    )
                    self.stack.append(
                        self.allocate(
                            "int[]",
                            list(range(len(items))),
                            span,
                        )
                    )
                    self.stack[-2], self.stack[-1] = self.stack[-1], self.stack[-2]
                else:
                    self.stack.append(
                        self.allocate(
                            typ if array_item(typ) is not None else "str[]", list(items), span
                        )
                    )
            elif op == "MAP":
                values = [self.pop(span) for _ in range(arg["count"])][::-1]
                self.stack.append(self.allocate(arg['type'], list(zip(values[::2], values[1::2])), span))
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
                mapped = isinstance(items, (dict, ValueMap))
                typ = self.value_type(value)
                parts = map_parts(typ) if mapped else None
                raw_index = index
                self.typed(index, parts[0] if mapped else "int", span)
                if mapped and is_typed_map(typ):
                    index = map_keys.wrap(self, index, parts[0], span)
                if mapped:
                    if op == "INDEX" and index not in items:
                        self.fail_data(
                            "Missing map key",
                            span,
                            code="collection.missing_key",
                            path=raw_index if isinstance(raw_index, str) else str(raw_index) if type(raw_index) in (int, float, bool) else '',
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
                    self.require_mutable(value, span)
                    self.typed(assigned, parts[1] if mapped else array_item(typ), span)
                    if mapped and index not in items:
                        if self.heap_items >= self.max_heap_items:
                            self.collect([value, assigned, raw_index])
                        if self.heap_items >= self.max_heap_items:
                            self.fail("Heap budget exceeded", span)
                        self.heap_items += 1
                    if type(items) is bytearray and not 0 <= assigned <= 255:
                        items = self.heap[value.address]["items"] = list(items)
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
                    value = a == b if type(a) in equality.SCALARS else equality.equal(self, a, b, span)
                    if op == "NE":
                        value = not value
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
            elif op == "STACK_DEPTH":
                self.stack.append(len(self.stack) - frame.base)
            elif op == "UNWIND":
                depth = frame.memory[arg]
                if type(depth) is not int or not 0 <= depth <= len(self.stack) - frame.base:
                    self.fail("Invalid operand unwind depth", span)
                boundary = frame.base + depth
                discarded = self.stack[boundary:]
                retained = {value.identity for value in self.stack[frame.base:boundary]
                            if isinstance(value, Access)}
                if any(isinstance(value, Access) and value.identity in retained for value in discarded):
                    self.fail("Cannot unwind a duplicated inout access", span)
                self.value_accesses.discard_pending(self, frame, discarded, span)
                del self.stack[boundary:]
            elif op == "JUMP":
                frame.pc = arg
            elif op in ("JUMP_IF_FALSE", "JUMP_IF_TRUE"):
                condition = self.typed(self.pop(span), "bool", span)
                if condition == (op == "JUMP_IF_TRUE"):
                    frame.pc = arg
            elif op in ("FUNCTION", "CLOSURE"):
                target = self.bytecode["functions"][arg]
                typ = self.callable_type(arg)
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
            elif op == "PROPAGATE":
                self._propagate(frame, arg, span)
            elif op == "RETURN":
                self._return_value(frame, self.pop(span), span)
            elif op == 'FLOW_NEW':
                count = len(self.bytecode['flows'][arg].get('initialInputs', []))
                args = [self.pop(span) for _ in range(count)][::-1]
                self.flow_runtime.create(self, arg, span, args)
            elif op == 'FLOW_STEP':
                signature = parse_type(arg).args[0]
                args = [self.pop(span) for _ in signature.args[:-1]][::-1]
                handle = self.typed(self.pop(span), arg, span)
                for value, parameter in zip(args, signature.args[:-1]):
                    self.typed(value, parameter.spelling(), span)
                self.flow_runtime.advance(self, handle, args, span)
            elif op == 'FLOW_CHILD_STEP':
                graph = self.bytecode['flows'][frame.function]
                child = next(item for item in graph['children'] if item['node'] == arg['node'])
                target = self.bytecode['functions'][child['function']]
                args = [self.pop(span) for _ in target['params']][::-1]
                for value, typ in zip(args, target['params']):
                    self.typed(value, typ, span)
                self.flow_runtime.active(self, frame, span)
                self._call(child['function'], args, flow_root=frame.flow_root,
                           flow_path=(*frame.flow_path, child['node']), flow_role='child')
            elif op == 'FLOW_CONFIG':
                self.stack.append(self.flow_runtime.configuration(self, frame, arg, span))
            elif op == 'FLOW_READ':
                self.stack.append(self.flow_runtime.read(self, frame, arg, span))
            elif op == 'FLOW_STAGE':
                self.flow_runtime.stage(self, frame, arg, self.pop(span), span)
            elif op == "TRAP":
                self.fail("Function reached unreachable terminator", span)
            elif op == 'SPATIAL_TRANSFORM':
                values = [self.pop(span) for _ in arg['fields']][::-1]
                fields = dict(zip(arg['fields'], values))
                typ = parse_type(arg['type'])
                source_id, target_id = [t.spelling() for t in typ.args]
                table = self.bytecode['coordinateFrames']
                source_frame = spatial.CoordinateFrame(source_id, table[source_id])
                target_frame = spatial.CoordinateFrame(target_id, table[target_id])
                translation = self.typed(fields['translation'], f'Vec3<{target_id}>', span)
                matrix = self.typed(fields['rotation'], 'float64[][]', span)
                rows = self.heap[matrix.address]['items']
                rotation = tuple(tuple(self.heap[self.typed(row, 'float64[]', span).address]['items']) for row in rows)
                try:
                    self.reserve_spatial(12, span)
                    self.stack.append(spatial.Transform(source_frame, target_frame, rotation, translation.xyz))
                except spatial.SpatialError as error:
                    self.fail_data(str(error), span, code='spatial.invalid_transform')
            elif op == 'SPACE_NEW':
                identity = self.typed(self.pop(span), 'str', span)
                if not identity or len(identity) > 128:
                    self.fail_data('Space identity must be nonempty and at most 128 characters', span,
                                   code='spatial.space_identity')
                if frame.flow_root is not None:
                    identity = self.flow_runtime.space_identity(self, frame, identity, span)
                if identity in self.space_identities:
                    self.fail_data('Space identity must be nonempty and unique in the runtime', span,
                                   code='spatial.space_identity')
                self.reserve_spatial(1, span)
                typ = parse_type(arg)
                frame_id = typ.args[0].spelling()
                coordinate_frame = spatial.CoordinateFrame(frame_id, self.bytecode['coordinateFrames'][frame_id])
                self.space_identities.add(identity)
                self.stack.append(spatial.SpaceValue(identity, coordinate_frame, typ.args[1].spelling(), typ.args[2].spelling()))
            elif op == 'SPACE_OP':
                args = [self.pop(span) for _ in range(spatial.OPERATIONS[arg])][::-1]
                try:
                    origin = (spatial.SpatialOrigin(frame.function, node)
                              if arg in ('transformPoint', 'transformVector') and type(node) is int else None)
                    self.stack.append(self.space_operation(arg, args, span, origin=origin))
                except spatial.SpatialError as error:
                    self.fail_data(str(error), span, code='spatial.invalid_operation')
            elif op == 'SPATIAL_VALUE':
                values = [self.pop(span) for _ in arg['fields']][::-1]
                fields = dict(zip(arg['fields'], values))
                typ = parse_type(arg['type'])
                identity = typ.args[0].spelling()
                try:
                    coordinate_frame = spatial.CoordinateFrame(identity, self.bytecode['coordinateFrames'][identity])
                    origin = spatial.SpatialOrigin(frame.function, node) if type(node) is int else None
                    self.stack.append(spatial.SpatialValue(typ.name, coordinate_frame, tuple(fields[k] for k in ('x', 'y', 'z')), origin))
                except spatial.SpatialError as error:
                    self.fail_data(str(error), span, code='spatial.invalid_value')
            elif op == 'SPATIAL_BINARY':
                b, a = self.pop(span), self.pop(span)
                origin = spatial.SpatialOrigin(frame.function, node) if type(node) is int else None
                try:
                    if arg == '+': result = spatial.add(a, b, origin=origin)
                    elif arg == '-': result = spatial.subtract(a, b, origin=origin)
                    elif arg == '*':
                        result = spatial.scale(a, b, origin=origin) if isinstance(a, spatial.SpatialValue) else spatial.scale(b, a, origin=origin)
                    else:
                        spatial.same_frame(a, b)
                        if a.kind != b.kind: raise spatial.SpatialError('Spatial equality requires matching types')
                        result = (a == b) if arg == '==' else (a != b)
                    self.stack.append(result)
                except spatial.SpatialError as error:
                    self.fail_data(str(error), span, code='spatial.invalid_operation')
            if self.stack:
                top = self.stack[-1]
                kind = type(top)
                if kind is int:
                    if not -LIMIT <= top < LIMIT:
                        self.fail("Signed 64-bit integer overflow", span)
                elif kind is float:
                    if not math.isfinite(top):
                        self.fail("Float64 arithmetic exceeds finite range", span)
                elif kind is str and len(top) > 1_000_000:
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
            if self.flow_runtime.transaction is not None:
                self.flow_runtime.abort(self)
            self.value_accesses.clear()
            self.halted = True
            self.error = e.to_dict()
            raise

    def run(self):
        while not self.halted:
            if self.event_waiting:
                raise PixelError('runtime', 'This program requires the native event runner',
                                 code='event.runner_required')
            self.step(snapshot=False)
        return deepcopy(self.output)
