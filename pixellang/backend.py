"""Representation-independent IR and deterministic bytecode assembly."""

from .typesys import ERROR_FIELDS

BINARY = {
    "+": "ADD",
    "-": "SUB",
    "*": "MUL",
    "/": "DIV",
    "%": "MOD",
    "==": "EQ",
    "!=": "NE",
    "<": "LT",
    "<=": "LE",
    ">": "GT",
    ">=": "GE",
}


class Lowerer:
    def __init__(self):
        self.counter, self.code = 0, []

    def label(self):
        self.counter += 1
        return f"L{self.counter}"

    def emit(self, op, arg=None, span=None):
        ins = {"op": op}
        if arg is not None:
            ins["arg"] = arg
        if span:
            ins["span"] = span.to_dict()
        self.code.append(ins)

    def expr(self, n):
        d = n.data
        if n.kind == "literal":
            self.emit("CONST", {"type": d["type"], "value": d["value"]}, n.span)
        elif n.kind == "enum_value":
            for argument in d["args"]:
                self.expr(argument)
            self.emit(
                "ENUM",
                {
                    "type": d["enum_type"],
                    "variant": d["variant"],
                    "fields": d["_fields"],
                },
                n.span,
            )
        elif n.kind == "match":
            self.match(n)
        elif n.kind == "record_value":
            for value in d["items"]:
                self.expr(value)
            self.emit(
                "RECORD", {"type": d["record_type"], "fields": d["names"]}, n.span
            )
        elif n.kind == "field":
            self.expr(d["target"])
            self.emit("FIELD", d["name"], n.span)
        elif n.kind in ("array", "map"):
            for item in d["items"]:
                self.expr(item)
            self.emit(
                "MAP" if n.kind == "map" else "ARRAY",
                {"type": d["_type"], "count": len(d["items"])},
                n.span,
            )
        elif n.kind == "index":
            self.expr(d["target"])
            self.expr(d["index"])
            self.emit("INDEX", span=n.span)
        elif n.kind == "json_decode":
            self.expr(d["value"])
            self.emit("JSON_DECODE", d["type"], n.span)
        elif n.kind == "builtin":
            for a in d["args"]:
                self.expr(a)
            self.emit("BUILTIN", d["name"], n.span)
        elif n.kind == "variable":
            self.emit(
                "FUNCTION", d["_function"], n.span
            ) if "_function" in d else self.emit("LOAD", d["_slot"], n.span)
        elif n.kind == "unary":
            self.expr(d["operand"])
            self.emit("NOT" if d["op"] == "not" else "NEG", span=n.span)
        elif n.kind == "lambda":
            for slot in d["_capture_slots"]:
                self.emit("CAPTURE", slot, n.span)
            self.emit("CLOSURE", d["_function"], n.span)
        elif n.kind == "function_value":
            self.emit("FUNCTION", d["_function"], n.span)
        elif n.kind == "invoke":
            self.expr(d["target"])
            for argument in d["args"]:
                self.expr(argument)
            self.emit("INVOKE", d["_callable_type"], n.span)
        elif n.kind == "call":
            if "_callable_type" in d:
                self.emit("LOAD", d["_slot"], n.span)
            for a in d["args"]:
                self.expr(a)
            self.emit(
                "INVOKE", d["_callable_type"], n.span
            ) if "_callable_type" in d else self.emit("CALL", d["_function"], n.span)
        elif n.kind == "binary":
            self.expr(d["left"])
            if d["op"] in ("and", "or"):
                end = self.label()
                self.emit("DUP", span=n.span)
                self.emit(
                    "JUMP_IF_FALSE" if d["op"] == "and" else "JUMP_IF_TRUE", end, n.span
                )
                self.emit("POP", span=n.span)
                self.expr(d["right"])
                self.emit("LABEL", end)
            else:
                self.expr(d["right"])
                self.emit(
                    "CONCAT"
                    if d["op"] == "+" and d["_type"] == "str"
                    else BINARY[d["op"]],
                    span=n.span,
                )

    def assignment_value(self, n):
        self.expr(n.data["value"])
        if "update" in n.data:
            self.emit(
                "CONCAT"
                if n.data["_update_type"] == "str"
                else BINARY[n.data["update"]],
                span=n.span,
            )

    def match(self, n):
        d = n.data
        self.expr(d["value"])
        self.emit("STORE", d["_slot"], n.span)
        end = self.label()
        for case in d["cases"]:
            c = case.data
            advance = self.label()
            if c["variant"] is not None:
                self.emit("LOAD", d["_slot"], case.span)
                self.emit(
                    "ENUM_IS",
                    {"type": c["enum_type"], "variant": c["variant"]},
                    case.span,
                )
                self.emit("JUMP_IF_FALSE", advance, case.span)
                for parameter in c["params"]:
                    self.emit("LOAD", d["_slot"], case.span)
                    self.emit(
                        "ENUM_GET",
                        {
                            "type": c["enum_type"],
                            "variant": c["variant"],
                            "field": parameter["_field"],
                        },
                        case.span,
                    )
                    self.emit("BIND", parameter["_slot"], case.span)
            if n.kind == "match":
                self.expr(c["value"])
            else:
                self.block(c["body"])
            self.emit("JUMP", end, case.span)
            self.emit("LABEL", advance)
        self.emit("TRAP", span=n.span)
        self.emit("LABEL", end)

    def block(self, nodes):
        for n in nodes:
            d = n.data
            if n.kind in ("fn", "import", "record", "enum"):
                continue
            if n.kind == "match_stmt":
                self.match(n)
            elif n.kind in ("let", "set"):
                if "update" in d:
                    self.emit("LOAD", d["_slot"], n.span)
                self.assignment_value(n)
                self.emit("BIND" if n.kind == "let" else "STORE", d["_slot"], n.span)
            elif n.kind == "field_set":
                self.expr(d["target"])
                if "update" in d:
                    self.emit("DUP", span=n.span)
                    self.emit("FIELD", d["name"], n.span)
                self.assignment_value(n)
                self.emit("FIELD_SET", d["name"], n.span)
            elif n.kind == "index_set":
                self.expr(d["target"])
                self.expr(d["index"])
                if "update" in d:
                    self.emit("DUP2", span=n.span)
                    self.emit("INDEX", span=n.span)
                self.assignment_value(n)
                self.emit("INDEX_SET", span=n.span)
            elif n.kind == "expr":
                self.expr(d["value"])
                self.emit("POP", span=n.span)
            elif n.kind in ("print", "return"):
                if d["value"] is None:
                    self.emit("CONST", {"type": "int", "value": 0}, n.span)
                else:
                    self.expr(d["value"])
                self.emit("PRINT" if n.kind == "print" else "RETURN", span=n.span)
            elif n.kind in ("break", "continue"):
                advance, end, depth = self.loops[-1]
                for _ in range(self.handler_depth - depth):
                    self.emit("END_TRY", span=n.span)
                self.emit("JUMP", end if n.kind == "break" else advance, n.span)
            elif n.kind == "for":
                start, advance, end = self.label(), self.label(), self.label()
                self.expr(d["value"])
                paired = "value_name" in d
                self.emit("ITER_SNAPSHOT", "pair" if paired else "single", n.span)
                if paired:
                    self.emit("STORE", d["_values_slot"], n.span)
                self.emit("STORE", d["_iter_slot"], n.span)
                self.emit("CONST", {"type": "int", "value": 0}, n.span)
                self.emit("STORE", d["_index_slot"], n.span)
                self.emit("LABEL", start)
                self.emit("LOAD", d["_index_slot"], n.span)
                self.emit("LOAD", d["_iter_slot"], n.span)
                self.emit("BUILTIN", "len", n.span)
                self.emit("LT", span=n.span)
                self.emit("JUMP_IF_FALSE", end, n.span)
                self.emit("LOAD", d["_iter_slot"], n.span)
                self.emit("LOAD", d["_index_slot"], n.span)
                self.emit("INDEX", span=n.span)
                self.emit("BIND", d["_slot"], n.span)
                if paired:
                    self.emit("LOAD", d["_values_slot"], n.span)
                    self.emit("LOAD", d["_index_slot"], n.span)
                    self.emit("INDEX", span=n.span)
                    self.emit("BIND", d["_value_slot"], n.span)
                self.loops.append((advance, end, self.handler_depth))
                self.block(d["body"])
                self.loops.pop()
                self.emit("LABEL", advance)
                self.emit("LOAD", d["_index_slot"], n.span)
                self.emit("CONST", {"type": "int", "value": 1}, n.span)
                self.emit("ADD", span=n.span)
                self.emit("STORE", d["_index_slot"], n.span)
                self.emit("JUMP", start, n.span)
                self.emit("LABEL", end)
            elif n.kind == "try":
                handler, end = self.label(), self.label()
                self.emit("TRY", handler, n.span)
                self.handler_depth += 1
                self.block(d["body"])
                self.handler_depth -= 1
                self.emit("END_TRY", span=n.span)
                self.emit("JUMP", end, n.span)
                self.emit("LABEL", handler)
                self.emit("BIND", d["_slot"], n.span)
                self.block(d["otherwise"])
                self.emit("LABEL", end)
            elif n.kind == "if":
                other, end = self.label(), self.label()
                self.expr(d["condition"])
                self.emit("JUMP_IF_FALSE", other, n.span)
                self.block(d["body"])
                self.emit("JUMP", end, n.span)
                self.emit("LABEL", other)
                self.block(d["otherwise"])
                self.emit("LABEL", end)
            elif n.kind == "while":
                start, end = self.label(), self.label()
                self.emit("LABEL", start)
                self.expr(d["condition"])
                self.emit("JUMP_IF_FALSE", end, n.span)
                self.loops.append((start, end, self.handler_depth))
                self.block(d["body"])
                self.loops.pop()
                self.emit("JUMP", start, n.span)
                self.emit("LABEL", end)

    def function(self, nodes, params, locals_count, result, span):
        self.code = []
        self.loops, self.handler_depth = [], 0
        self.block(nodes)
        # Unit functions may fall through; other functions must return on every path.
        if result == "unit":
            self.emit("CONST", {"type": "int", "value": 0}, span)
            self.emit("RETURN", span=span)
        else:
            self.emit("TRAP", span=span)
        return {
            "params": params,
            "locals": locals_count,
            "result": result,
            "instructions": self.code,
        }


def lower(modules):
    lowerer = Lowerer()
    functions = {}
    for key, n in modules["main"].data["_function_instances"].items():
        d = n.data
        functions[key] = lowerer.function(
            d["body"], [p["type"] for p in d["params"]], d["_locals"], d["type"], n.span
        )
        functions[key]["captures"] = d.get("_capture_types", [])
    return {
        "version": max(t.data.get("_language", "0.8") for t in modules.values()),
        "records": {
            "Error": dict(ERROR_FIELDS),
            **modules["main"].data["_record_instances"],
        },
        "enums": modules["main"].data["_enum_instances"],
        "entry": [
            f"{mid}:{n.data['name']}"
            for mid, tree in modules.items()
            for n in tree.data["body"]
            if n.kind == "fn" and n.data.get("entry")
        ],
        "functions": functions,
    }


def optimize(ir):
    """Eliminate jumps to the immediately following label; preserve all provenance."""
    import copy

    result = copy.deepcopy(ir)
    for fn in result["functions"].values():
        code = fn["instructions"]
        fn["instructions"] = [
            ins
            for i, ins in enumerate(code)
            if not (
                ins["op"] == "JUMP"
                and i + 1 < len(code)
                and code[i + 1]["op"] == "LABEL"
                and ins["arg"] == code[i + 1]["arg"]
            )
        ]
    return result


def assemble(ir):
    constants, indices, functions = [], {}, {}
    for name, fn in ir["functions"].items():
        labels, pc = {}, 0
        for ins in fn["instructions"]:
            if ins["op"] == "LABEL":
                labels[ins["arg"]] = pc
            else:
                pc += 1
        code = []
        for ins in fn["instructions"]:
            if ins["op"] == "LABEL":
                continue
            out = dict(ins)
            if ins["op"] == "CONST":
                c = ins["arg"]
                key = (
                    c["type"],
                    c["value"].hex() if c["type"] == "float64" else c["value"],
                )
                if key not in indices:
                    indices[key] = len(constants)
                    constants.append(c)
                out = {**out, "op": "PUSH", "arg": indices[key]}
            elif ins["op"].startswith("JUMP") or ins["op"] == "TRY":
                out["arg"] = labels[ins["arg"]]
            code.append(out)
        functions[name] = {**fn, "instructions": code}
    return {
        "magic": "PIXELVM",
        "vm_version": ir["version"],
        "language_version": ir["version"],
        "compiler_version": "0.8.0",
        "records": ir.get("records", {}),
        "enums": ir.get("enums", {}),
        "constants": constants,
        "entry": ir["entry"],
        "functions": functions,
    }
