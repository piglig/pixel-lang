"""Lexical scope, function signatures, definite assignment and static types."""

import math

from .captures import analyze_captures
from .model import Node, PixelError
from .specialize import function_instance
from .typeexpr import parse_type
from .typesys import ERROR_FIELDS, base_type, builtin_type, is_record, valid_type


class Checker:
    def __init__(self, modules, tolerant=False, require_entry=True, type_names=None):
        self.type_names = type_names or {}
        self.tolerant, self.errors = tolerant, []
        self.lambda_instances = {}
        self.completion_outer = {}
        self.require_entry = require_entry
        self.modules, self.functions, self.imports = modules, {}, {}
        self.records = {}
        self.record_instances = {}
        self.enums, self.enum_instances = {}, {}
        self.type_parameters = set()
        self.type_access = set()
        self.function_instances = {}
        self.generic_function_count = 0
        for mid, tree in modules.items():
            self.imports[mid] = set()
            for n in tree.data["body"]:
                if n.kind == "import":
                    dep = n.data["module"]
                    if dep in self.imports[mid]:
                        self.fail(n, "Duplicate import")
                    self.imports[mid].add(dep)
                if n.kind in ("record", "enum"):
                    key = f"@{0 if mid == 'main' else mid}:{n.data['name']}"
                    if key in self.records or key in self.enums:
                        self.fail(n, "Duplicate nominal type")
                    (self.records if n.kind == "record" else self.enums)[key] = n
                if n.kind == "fn":
                    key = f"{mid}:{n.data['name']}"
                    if key in self.functions:
                        self.fail(n, "Duplicate function identifier")
                    self.functions[key] = n

    def fail(self, n, message, phase="semantic"):
        raise PixelError(phase, message, n.span)

    def require(self, n, actual, expected):
        if actual != expected:
            self.fail(
                n,
                f"Expected {self.display_type(expected)}, got {self.display_type(actual)}",
                "type",
            )

    def display_type(self, typ):
        return parse_type(typ).display(lambda name: self.type_names.get(name, name))

    def record_schema(self, typ, n):
        if typ == "Error":
            return ERROR_FIELDS
        value = parse_type(typ)
        record = self.records.get(value.name)
        if record is None:
            self.fail(n, f"Unknown record type {typ}", "type")
        module = value.name[1:].split(":")[0]
        current = str(0 if self.mid == "main" else self.mid)
        if (
            module != current
            and not record.data["export"]
            and value.name not in self.type_access
        ):
            self.fail(n, "Record type is private", "type")
        params = record.data.get("type_params", [])
        if len(params) != len(value.args):
            self.fail(
                n,
                f"Record expects {len(params)} type arguments, got {len(value.args)}",
                "type",
            )
        bindings = {str(p["name"]): a for p, a in zip(params, value.args)}
        schema = {
            field["name"]: parse_type(field["type"]).substitute(bindings).spelling()
            for field in record.data["fields"]
        }
        if not any(t.kind == "parameter" for t in value.walk()):
            if len(self.record_instances) >= 256 and typ not in self.record_instances:
                self.fail(
                    n,
                    "Record instantiation limit exceeded (possibly expanding recursive type)",
                    "type",
                )
            self.record_instances[typ] = schema
        return schema

    def check_type(self, typ, n):
        if not valid_type(typ, self.type_parameters):
            self.fail(n, "Invalid declared type", "type")
        for item in parse_type(typ).walk():
            if item.kind == "nominal":
                if self.is_enum(item.spelling()):
                    self.enum_schema(item.spelling(), n)
                else:
                    self.record_schema(item.spelling(), n)

    def is_enum(self, typ):
        value = parse_type(typ)
        return value.kind == "nominal" and (
            value.name == "Option" or value.name in self.enums
        )

    def enum_schema(self, typ, n):
        value = parse_type(typ)
        if value.name == "Option":
            if len(value.args) != 1:
                self.fail(n, "Option requires one type argument", "type")
            schema = {"None": {}, "Some": {"value": value.args[0].spelling()}}
        else:
            declaration = self.enums.get(value.name)
            if declaration is None:
                self.fail(n, "Expected an enum type", "type")
            module = value.name[1:].split(":")[0]
            if (
                module != str(0 if self.mid == "main" else self.mid)
                and not declaration.data["export"]
                and value.name not in self.type_access
            ):
                self.fail(n, "Enum type is private", "type")
            params = declaration.data["type_params"]
            if len(params) != len(value.args):
                self.fail(n, f"Enum expects {len(params)} type arguments", "type")
            bindings = {str(p["name"]): arg for p, arg in zip(params, value.args)}
            schema = {
                variant.data["name"]: {
                    p["name"]: parse_type(p["type"]).substitute(bindings).spelling()
                    for p in variant.data["params"]
                }
                for variant in declaration.data["variants"]
            }
        if not any(t.kind == "parameter" for t in value.walk()):
            if typ not in self.enum_instances and len(self.enum_instances) >= 256:
                self.fail(n, "Enum instantiation limit exceeded", "type")
            self.enum_instances[typ] = schema
        return schema

    def match(self, n, expected=None, return_type=None, loop_depth=0):
        d = n.data
        subject = self.expr(d["value"])
        schema = self.enum_schema(subject, n)
        if self.next_slot >= 65536:
            self.fail(n, "Local binding limit exceeded")
        d["_slot"] = self.next_slot
        self.next_slot += 1
        seen, result, all_return = set(), None, True
        for index, case in enumerate(d["cases"]):
            c = case.data
            tag = c["variant"]
            if tag is None:
                if index != len(d["cases"]) - 1 or seen == set(schema):
                    self.fail(case, "Wildcard must be final and reachable", "type")
                fields = {}
                seen = set(schema)
            else:
                if tag in seen:
                    self.fail(case, "Duplicate match variant", "type")
                seen.add(tag)
                if self.tolerant and c.get("_incomplete") and c["enum_type"] is None:
                    c["enum_type"] = subject
                pattern = parse_type(c["enum_type"])
                if not pattern.args and pattern.name == parse_type(subject).name:
                    c["enum_type"] = subject
                self.require(case, c["enum_type"], subject)
                if tag not in schema:
                    self.fail(case, "Unknown enum variant", "type")
                fields = schema[tag]
            if len(c["params"]) != len(fields):
                self.fail(case, "Match payload binding count mismatch", "type")
            self.scopes.append({})
            try:
                for parameter, (field, typ) in zip(c["params"], fields.items()):
                    parameter["type"], parameter["_field"] = typ, field
                    parameter["_slot"] = self.declare(case, parameter["name"], typ)
                if n.kind == "match":
                    actual = self.expr(c["value"], expected)
                    if result is not None:
                        self.require(case, actual, result)
                    result = actual
                else:
                    all_return &= self.block(c["body"], return_type, True, loop_depth)
            finally:
                self.scopes.pop()
        if seen != set(schema):
            self.fail(
                n,
                "Non-exhaustive match; missing "
                + ", ".join(sorted(set(schema) - seen)),
                "type",
            )
        return result if n.kind == "match" else all_return

    def lookup(self, n):
        for scope in reversed(self.scopes):
            if n.data["name"] in scope:
                return scope[n.data["name"]]
        self.fail(n, f"Undefined variable #{n.data['name']}")

    def declare(self, n, name, typ, mutable=False):
        if f"@{0 if self.mid == 'main' else self.mid}:{name}" in self.enums:
            self.fail(n, "Binding name conflicts with an enum type")
        if name in self.scopes[-1]:
            self.fail(n, f"Duplicate variable #{name} in scope")
        if self.next_slot >= 65536:
            self.fail(n, "Local binding limit exceeded")
        self.check_type(typ, n)
        slot = self.next_slot
        self.next_slot += 1
        self.scopes[-1][name] = (typ, slot, mutable)
        return slot

    def assignment_value(self, n, typ):
        self.require(n, self.expr(n.data["value"], typ), typ)
        if "update" in n.data:
            op = n.data["update"]
            if op not in ("+", "-", "*", "/", "%") or not (
                typ in ("int", "float64") or typ == "str" and op == "+"
            ):
                self.fail(n, f"Operator {op}= is not defined for {typ}", "type")
            n.data["_update_type"] = typ

    def infer_call(self, n, fn, expected=None):
        """Solve invariant argument constraints without guessing or coercing types."""
        parameters = {str(p["name"]) for p in fn.data["type_params"]}
        bindings, checked = {}, {}
        args, formals = n.data["args"], fn.data["params"]
        if len(args) != len(formals):
            self.fail(n, "Function argument count mismatch")

        def unify(pattern, actual, node):
            if pattern.kind == "parameter" and pattern.name in parameters:
                previous = bindings.get(pattern.name)
                if previous is not None and previous != actual:
                    self.fail(
                        node,
                        "Conflicting generic argument constraints: "
                        f"{self.display_type(previous.spelling())} and {self.display_type(actual.spelling())}; "
                        "use consistent argument types or explicit type arguments",
                        "type",
                    )
                bindings[pattern.name] = actual
            elif (pattern.kind, pattern.name, len(pattern.args)) != (
                actual.kind,
                actual.name,
                len(actual.args),
            ):
                self.fail(
                    node,
                    f"Cannot infer generic arguments: expected {self.display_type(pattern.spelling())}, "
                    f"got {self.display_type(actual.spelling())}",
                    "type",
                )
            else:
                for child, value in zip(pattern.args, actual.args):
                    unify(child, value, node)

        def needs_context(node):
            if node.kind == "lambda":
                return node.data["type"] is None or any(
                    p["type"] is None for p in node.data["params"]
                )
            if node.data.get("_declared_type"):
                return False
            if node.kind == "array":
                return not node.data["items"] or any(
                    needs_context(i) for i in node.data["items"]
                )
            return (
                node.kind == "enum_value"
                and node.data["enum_type"] == "Option"
                and node.data["variant"] == "None"
            )

        result_pattern = parse_type(fn.data["type"])
        if expected is not None and any(
            t.kind == "parameter" and t.name in parameters
            for t in result_pattern.walk()
        ):
            unify(result_pattern, parse_type(expected), n)

        # Independent evidence first permits f([], typedValue) as well as the
        # reversed order. This changes checking order, never runtime evaluation.
        deferred = []
        for index, (arg, formal) in enumerate(zip(args, formals)):
            pattern = parse_type(formal["type"])
            if needs_context(arg):
                deferred.append((index, arg, pattern))
            else:
                checked[index] = self.expr(arg)
                unify(pattern, parse_type(checked[index]), arg)
        while deferred:
            remaining = []
            for index, arg, pattern in deferred:
                context = pattern.substitute(bindings)
                needed = context
                if arg.kind == "lambda" and context.kind == "function":
                    # A callback body can contribute its result type once all
                    # omitted input parameter types have independent evidence.
                    missing = {
                        t.name
                        for p, typ in zip(arg.data["params"], context.args[:-1])
                        if p["type"] is None
                        for t in typ.walk()
                        if t.kind == "parameter" and t.name in parameters
                    }
                else:
                    missing = {
                        t.name
                        for t in needed.walk()
                        if t.kind == "parameter" and t.name in parameters
                    }
                if missing:
                    remaining.append((index, arg, pattern))
                    continue
                checked[index] = self.expr(arg, context.spelling())
                unify(pattern, parse_type(checked[index]), arg)
            if len(remaining) == len(deferred):
                self.fail(
                    remaining[0][1],
                    "Cannot infer generic arguments from an untyped value; "
                    "add an argument annotation or explicit type arguments",
                    "type",
                )
            deferred = remaining
        if parameters - bindings.keys():
            self.fail(
                n,
                "Cannot infer generic arguments absent from call arguments; "
                "supply explicit type arguments",
                "type",
            )
        return [
            bindings[str(p["name"])].spelling() for p in fn.data["type_params"]
        ], checked

    def expr(self, n, expected=None):
        d = n.data
        if (
            n.kind in ("call", "function_value")
            and d.get("_module_binding") is not None
            and any(d["_module_binding"] in scope for scope in self.scopes)
        ):
            if d.get("type_args"):
                self.fail(
                    n, "A function-valued field does not accept type arguments", "type"
                )
            target = Node(
                "variable",
                {"name": d["_module_binding"], "_name_span": d.get("_module_span")},
                n.span,
            )
            member = Node(
                "field",
                {
                    "target": target,
                    "name": d.get("_source_member_name", d["name"]),
                    "_name_span": d.get("_name_span"),
                },
                n.span,
            )
            if n.kind == "call":
                n.kind, n.data = "invoke", {"target": member, "args": d["args"]}
            else:
                n.kind, n.data = member.kind, member.data
            d = n.data
        if self.tolerant:
            d["_visible"] = {
                **self.completion_outer,
                **{
                    name: value
                    for scope in self.scopes
                    for name, value in scope.items()
                },
            }
        if n.kind == "literal":
            typ = d["type"]
            if typ == "int" and not -(2**63) <= d["value"] < 2**63:
                self.fail(n, "Integer literal exceeds signed 64-bit range", "type")
            if typ == "float64" and (
                type(d["value"]) is not float or not math.isfinite(d["value"])
            ):
                self.fail(n, "Float64 literal must be finite", "type")
        elif n.kind == "lambda":
            context = parse_type(expected) if expected else None
            if context is not None and context.kind != "function":
                self.fail(n, "Anonymous function requires a function context", "type")
            if context is not None and len(context.args) - 1 != len(d["params"]):
                self.fail(n, "Anonymous function parameter count mismatch", "type")
            for index, parameter in enumerate(d["params"]):
                if parameter["type"] is None:
                    if context is None:
                        self.fail(
                            n,
                            "Cannot infer anonymous parameter type; add an annotation or function context",
                            "type",
                        )
                    parameter["type"] = context.args[index].spelling()
            if d["type"] is None and context is not None:
                result = context.args[-1]
                if not any(
                    t.kind == "parameter" and t.name not in self.type_parameters
                    for t in result.walk()
                ):
                    d["type"] = result.spelling()
            captures = []
            for name in d["_capture_names"]:
                typ, slot, mutable = self.lookup(
                    Node("variable", {"name": name}, n.span)
                )
                captures.append((name, typ, slot, mutable))
            outer_scopes, outer_slot = self.scopes, self.next_slot
            previous_completion = self.completion_outer
            if self.tolerant:
                self.completion_outer = {
                    **previous_completion,
                    **{
                        name: value
                        for scope in self.scopes
                        for name, value in scope.items()
                    },
                }
            self.scopes, self.next_slot = [{}], 0
            try:
                for parameter in d["params"]:
                    parameter["_slot"] = self.declare(
                        n, parameter["name"], parameter["type"]
                    )
                for name, typ, _, mutable in captures:
                    self.declare(n, name, typ, mutable)
                if d["type"] is None:
                    inferred = []
                    returns = self.block(d["body"], inferred, True)
                    d["type"] = inferred[0] if inferred else "unit"
                    for actual in inferred[1:]:
                        self.require(n, actual, d["type"])
                else:
                    if d["type"] != "unit":
                        self.check_type(d["type"], n)
                    returns = self.block(d["body"], d["type"], True)
                if d["type"] != "unit" and not returns:
                    self.fail(
                        n,
                        "Anonymous function must return a value on every path",
                        "type",
                    )
                d["_locals"] = self.next_slot
            finally:
                self.scopes, self.next_slot = outer_scopes, outer_slot
                self.completion_outer = previous_completion
            d["_capture_slots"] = [slot for _, _, slot, _ in captures]
            d["_capture_types"] = [typ for _, typ, _, _ in captures]
            d["_function"] = self.current_function + "#" + str(d["_lambda_index"])
            if not self.type_parameters:
                self.lambda_instances[d["_function"]] = n
            typ = (
                "fn("
                + ",".join(p["type"] for p in d["params"])
                + ")->("
                + d["type"]
                + ")"
            )
        elif n.kind == "match":
            typ = self.match(n, expected)
        elif n.kind == "enum_value":
            typ = d["enum_type"]
            if typ == "Option":
                if expected and parse_type(expected).name == "Option":
                    typ = expected
                elif d["variant"] == "Some" and len(d["args"]) == 1:
                    typ = "Option<" + self.expr(d["args"][0]) + ">"
                else:
                    self.fail(
                        n, "Cannot infer Option type; add Option[T] context", "type"
                    )
                d["enum_type"] = typ
            fields = self.enum_schema(typ, n).get(d["variant"])
            if fields is None:
                self.fail(n, "Unknown enum variant", "type")
            if len(d["args"]) != len(fields):
                self.fail(n, "Enum payload argument count mismatch", "type")
            for arg, field_type in zip(d["args"], fields.values()):
                self.require(arg, self.expr(arg, field_type), field_type)
            d["_fields"] = list(fields)
        elif n.kind == "record_value":
            typ = d["record_type"]
            schema = self.record_schema(typ, n)
            if len(set(d["names"])) != len(d["names"]) or set(d["names"]) != set(
                schema
            ):
                self.fail(
                    n,
                    "Record constructor requires each declared field exactly once",
                    "type",
                )
            for name, value in zip(d["names"], d["items"]):
                self.require(value, self.expr(value, schema[name]), schema[name])
        elif n.kind == "field":
            container = self.expr(d["target"])
            if not is_record(container):
                self.fail(n, "Field access requires a record", "type")
            schema = self.record_schema(container, n)
            if d["name"] not in schema:
                self.fail(n, f"Unknown field {d['name']}", "type")
            typ = schema[d["name"]]
        elif n.kind == "map":
            declared = d.get("_declared_type") or expected
            if (
                not declared
                or not declared.endswith("{}")
                or not valid_type(declared, self.type_parameters)
            ):
                self.fail(n, "Map literal requires a map value type", "type")
            for i in range(0, len(d["items"]), 2):
                self.require(d["items"][i], self.expr(d["items"][i]), "str")
                self.require(
                    d["items"][i + 1],
                    self.expr(d["items"][i + 1], declared[:-2]),
                    declared[:-2],
                )
            typ = declared
        elif n.kind == "array":
            declared = d.get("_declared_type") or expected
            element = declared[:-2] if declared and declared.endswith("[]") else None
            types = [self.expr(item, element) for item in d["items"]]
            if not types and not element:
                self.fail(n, "Empty array needs an explicit type", "type")
            element = element or types[0]
            if not valid_type(element, self.type_parameters):
                self.fail(n, "Array elements require a value type", "type")
            for item, typ in zip(d["items"], types):
                self.require(item, typ, element)
            typ = element + "[]"
        elif n.kind == "index":
            container = self.expr(d["target"])
            self.require(
                d["index"],
                self.expr(d["index"]),
                "str" if container.endswith("{}") else "int",
            )
            if container != "str" and not container.endswith(("[]", "{}")):
                self.fail(n, "Indexing requires a string, array or map", "type")
            typ = "str" if container == "str" else container[:-2]
        elif n.kind == "json_decode":
            typ = d["type"]
            self.check_type(typ, n)
            seen = set()

            def check_target(target):
                base = base_type(target)
                if parse_type(base).kind == "function":
                    self.fail(n, "Function values cannot be decoded from JSON", "type")
                if base == "Error":
                    self.fail(
                        n, "Error is produced by catch, not JSON decoding", "type"
                    )
                if is_record(base) and base not in seen:
                    seen.add(base)
                    if self.is_enum(base):
                        value = parse_type(base)
                        if value.name == "Option" and value.args[0].name == "Option":
                            self.fail(
                                n,
                                "Adjacent nested Option types cannot be decoded unambiguously from JSON",
                                "type",
                            )
                        groups = self.enum_schema(base, n).values()
                    else:
                        groups = [self.record_schema(base, n)]
                    for group in groups:
                        for field_type in group.values():
                            check_target(field_type)

            check_target(typ)
            if self.expr(d["value"]) not in ("str", "json"):
                self.fail(n, "JSON decode input must be string or json", "type")
        elif n.kind == "builtin":
            if d.get("type_args"):
                self.fail(n, "Builtin does not accept type arguments", "type")
            try:
                typ = builtin_type(d["name"], [self.expr(a) for a in d["args"]])
            except ValueError as e:
                self.fail(n, str(e), "type")
        elif n.kind == "variable":
            key = f"{self.mid}:{d['name']}"
            if (
                any(d["name"] in scope for scope in self.scopes)
                or key not in self.functions
            ):
                typ, d["_slot"], _mutable = self.lookup(n)
            else:
                fn = self.functions[key]
                if fn.data.get("type_params"):
                    self.fail(
                        n, "Generic function value requires specialization", "type"
                    )
                d["_function"] = key
                typ = (
                    "fn("
                    + ",".join(p["type"] for p in fn.data["params"])
                    + ")->("
                    + fn.data["type"]
                    + ")"
                )
        elif n.kind == "unary":
            actual = self.expr(d["operand"])
            typ = "bool" if d["op"] == "not" else actual
            if d["op"] != "not" and actual not in ("int", "float64"):
                self.fail(n, "Negation requires a numeric value", "type")
            self.require(n, actual, typ)
        elif n.kind == "binary":
            a, b = self.expr(d["left"]), self.expr(d["right"])
            op = d["op"]
            if op in ("==", "!="):
                self.require(n, a, b)
                if (
                    a.endswith(("[]", "{}"))
                    or is_record(a)
                    or a == "json"
                    or a.startswith("$")
                    or parse_type(a).kind == "function"
                ):
                    self.fail(n, "Compare array elements explicitly", "type")
                typ = "bool"
            elif op == "+" and a == b == "str":
                typ = "str"
            elif op in ("<", "<=", ">", ">=") and a == b == "str":
                typ = "bool"
            else:
                expected = (
                    "bool"
                    if op in ("and", "or")
                    else ("float64" if a == "float64" else "int")
                )
                self.require(d["left"], a, expected)
                self.require(d["right"], b, expected)
                typ = "bool" if op in ("<", "<=", ">", ">=", "and", "or") else expected
        elif (
            n.kind == "call"
            and d["module"] is None
            and any(d["name"] in scope for scope in self.scopes)
        ):
            callable_type, d["_slot"], _ = self.lookup(n)
            signature = parse_type(callable_type)
            if signature.kind != "function":
                self.fail(n, "Call target must have a function type", "type")
            if d.get("type_args") or len(d["args"]) != len(signature.args) - 1:
                self.fail(
                    n, "Indirect call argument count or type arguments mismatch", "type"
                )
            for arg, parameter in zip(d["args"], signature.args[:-1]):
                self.require(
                    arg, self.expr(arg, parameter.spelling()), parameter.spelling()
                )
            d["_callable_type"] = callable_type
            typ = signature.args[-1].spelling()
        elif n.kind == "invoke":
            callable_type = self.expr(d["target"])
            signature = parse_type(callable_type)
            if signature.kind != "function":
                self.fail(n, "Call target must have a function type", "type")
            if len(d["args"]) != len(signature.args) - 1:
                self.fail(n, "Indirect call argument count mismatch", "type")
            for arg, parameter in zip(d["args"], signature.args[:-1]):
                self.require(
                    arg, self.expr(arg, parameter.spelling()), parameter.spelling()
                )
            d["_callable_type"] = callable_type
            typ = signature.args[-1].spelling()
        elif n.kind in ("call", "function_value"):
            module = self.mid if d["module"] is None else d["module"]
            if d["module"] is not None and module not in self.imports[self.mid]:
                self.fail(n, f"Module #{module} is not imported")
            key = f"{module}:{d['name']}"
            fn = self.functions.get(key)
            if not fn:
                self.fail(n, f"Undefined function {key}")
            if module != self.mid and not fn.data["export"]:
                self.fail(n, f"Function {key} is private")
            d["_declaration"] = key
            type_args = d.get("type_args", [])
            parameters = fn.data.get("type_params", [])
            inferred_args = {}
            if parameters and not type_args and n.kind == "call":
                type_args, inferred_args = self.infer_call(n, fn, expected)
            if len(type_args) != len(parameters):
                self.fail(
                    n,
                    f"Function expects {len(parameters)} type arguments, got {len(type_args)}",
                    "type",
                )
            if parameters:
                for argument in type_args:
                    self.check_type(argument, n)
                arguments = tuple(parse_type(t) for t in type_args)
                key += "<" + ",".join(type_args) + ">"
                concrete = not any(
                    t.kind == "parameter" for a in arguments for t in a.walk()
                )
                if key in self.function_instances:
                    fn = self.function_instances[key]
                else:
                    fn = function_instance(fn, arguments)
                    fn.data["_type_arguments"] = type_args
                    fn.data["_type_access"] = sorted(
                        {
                            t.name
                            for a in arguments
                            for t in a.walk()
                            if t.kind == "nominal"
                        }
                    )
                    if concrete:
                        if self.generic_function_count >= 256:
                            self.fail(
                                n, "Function instantiation limit exceeded", "type"
                            )
                        self.function_instances[key] = fn
                        self.generic_function_count += 1
            if n.kind == "function_value":
                typ = (
                    "fn("
                    + ",".join(p["type"] for p in fn.data["params"])
                    + ")->("
                    + fn.data["type"]
                    + ")"
                )
            else:
                if len(d["args"]) != len(fn.data["params"]):
                    self.fail(n, "Function argument count mismatch")
                for index, (arg, param) in enumerate(zip(d["args"], fn.data["params"])):
                    actual = (
                        inferred_args[index]
                        if index in inferred_args
                        else self.expr(arg, param["type"])
                    )
                    self.require(arg, actual, param["type"])
                typ = fn.data["type"]
            d["_function"] = key
        else:
            self.fail(n, "Unknown expression")
        if typ != "unit":
            self.check_type(typ, n)
        d["_type"] = typ
        return typ

    def block(self, nodes, return_type=None, nested=False, loop_depth=0):
        if nested:
            self.scopes.append({})
        always_returns = False
        terminated = False
        for n in nodes:
            saved_scopes = (
                [scope.copy() for scope in self.scopes] if self.tolerant else None
            )
            saved_slot, saved_returns = self.next_slot, always_returns
            saved_terminated = terminated
            try:
                d = n.data
                if always_returns or terminated:
                    self.fail(
                        n, "Unreachable statement after unconditional control transfer"
                    )
                if n.kind in ("fn", "import", "record", "enum"):
                    if nested or return_type:
                        self.fail(n, "Functions and imports must be module-level")
                    continue
                if n.kind == "match_stmt":
                    always_returns = self.match(
                        n, return_type=return_type, loop_depth=loop_depth
                    )
                elif n.kind == "let":
                    typ = self.expr(d["value"], d.get("type"))
                    if d.get("type") is None:
                        d["type"] = typ
                    self.require(n, typ, d["type"])
                    d["_slot"] = self.declare(
                        n, d["name"], d["type"], d.get("mutable", False)
                    )
                elif n.kind == "set":
                    typ, d["_slot"], mutable = self.lookup(n)
                    if not mutable:
                        self.fail(
                            n, "Cannot reassign immutable binding; declare it with var"
                        )
                    self.assignment_value(n, typ)
                elif n.kind == "field_set":
                    container = self.expr(d["target"])
                    if container == "Error":
                        self.fail(n, "Error fields are read-only", "type")
                    schema = (
                        self.record_schema(container, n) if is_record(container) else {}
                    )
                    if d["name"] not in schema:
                        self.fail(n, f"Unknown record field {d['name']}", "type")
                    typ = schema[d["name"]]
                    self.assignment_value(n, typ)
                elif n.kind == "index_set":
                    container = self.expr(d["target"])
                    if not container.endswith(("[]", "{}")):
                        self.fail(
                            n, "Only arrays and maps support index assignment", "type"
                        )
                    self.require(
                        d["index"],
                        self.expr(d["index"]),
                        "str" if container.endswith("{}") else "int",
                    )
                    self.assignment_value(n, container[:-2])
                elif n.kind in ("print", "expr"):
                    typ = self.expr(d["value"])
                    if n.kind == "print" and typ == "unit":
                        self.fail(
                            n, "Cannot print a function with no return value", "type"
                        )
                elif n.kind == "return":
                    if return_type is None:
                        self.fail(n, "Return is only valid in a function")
                    if isinstance(return_type, list):
                        return_type.append(
                            self.expr(d["value"]) if d["value"] is not None else "unit"
                        )
                    elif return_type == "unit":
                        if d["value"] is not None:
                            self.fail(n, "Void function cannot return a value", "type")
                    else:
                        if d["value"] is None:
                            self.fail(n, "Return requires a value", "type")
                        self.require(n, self.expr(d["value"], return_type), return_type)
                    always_returns = True
                elif n.kind in ("break", "continue"):
                    if not loop_depth:
                        self.fail(n, f"{n.kind} is only valid inside a loop")
                    terminated = True
                elif n.kind == "for":
                    container = self.expr(d["value"])
                    if container != "str" and not container.endswith(("[]", "{}")):
                        self.fail(
                            n,
                            "Collection iteration requires a string, array or map",
                            "type",
                        )
                    element = container[:-2] if container.endswith("[]") else "str"
                    paired = "value_name" in d
                    self.scopes.append({})
                    d["_slot"] = self.declare(
                        n,
                        d["name"],
                        ("str" if container.endswith("{}") else "int")
                        if paired
                        else element,
                    )
                    if paired:
                        value_type = (
                            container[:-2]
                            if container.endswith(("[]", "{}"))
                            else "str"
                        )
                        d["_value_slot"] = self.declare(n, d["value_name"], value_type)
                        d["_values_slot"] = self.next_slot
                        self.next_slot += 1
                    if self.next_slot + 2 > 65536:
                        self.fail(n, "Local binding limit exceeded")
                    d["_iter_slot"], d["_index_slot"] = (
                        self.next_slot,
                        self.next_slot + 1,
                    )
                    self.next_slot += 2
                    self.block(d["body"], return_type, loop_depth=loop_depth + 1)
                    self.scopes.pop()
                elif n.kind == "try":
                    a = self.block(d["body"], return_type, True, loop_depth)
                    self.scopes.append({})
                    d["_slot"] = self.declare(n, d["name"], "Error")
                    b = self.block(d["otherwise"], return_type, loop_depth=loop_depth)
                    self.scopes.pop()
                    always_returns = a and b
                elif n.kind in ("if", "while"):
                    self.require(n, self.expr(d["condition"]), "bool")
                    a = self.block(
                        d["body"], return_type, True, loop_depth + (n.kind == "while")
                    )
                    if n.kind == "if":
                        b = self.block(d["otherwise"], return_type, True, loop_depth)
                        always_returns = a and b
                else:
                    self.fail(n, "Unknown statement")
            except PixelError as error:
                if not self.tolerant:
                    raise
                n.data["_invalid"] = True
                self.errors.append(error)
                terminated = saved_terminated
                self.scopes, self.next_slot, always_returns = (
                    saved_scopes,
                    saved_slot,
                    saved_returns,
                )
        if nested:
            self.scopes.pop()
        return always_returns

    def check_function(self, n):
        self.current_function = f"{self.mid}:{n.data['name']}" + (
            "<" + ",".join(n.data["_type_arguments"]) + ">"
            if n.data.get("_type_arguments")
            else ""
        )
        plan = analyze_captures(Node("program", {"body": [n]}, n.span))
        for scope in plan.functions[1:]:
            scope.node.data["_capture_names"] = [
                plan.bindings[i].name for i in scope.captures
            ]
            scope.node.data["_lambda_index"] = scope.identity
        self.type_access = set(n.data.get("_type_access", []))
        parameters = [str(p["name"]) for p in n.data.get("type_params", [])]
        if len(parameters) > 32 or len(parameters) != len(set(parameters)):
            self.fail(n, "Invalid or duplicate type parameters", "type")
        self.type_parameters = set(parameters)
        if n.data["type"] != "unit":
            self.check_type(n.data["type"], n)
        self.scopes, self.next_slot = [{}], 0
        for p in n.data["params"]:
            p["_slot"] = self.declare(n, p["name"], p["type"])
        returns = self.block(n.data["body"], n.data["type"], True)
        if not returns and n.data["type"] != "unit":
            if self.tolerant:
                self.errors.append(
                    PixelError(
                        "semantic", "Function must return a value on every path", n.span
                    )
                )
            else:
                self.fail(n, "Function must return a value on every path")
        n.data["_locals"] = self.next_slot
        self.type_parameters = set()
        self.type_access = set()

    def check(self):
        entries = []
        for mid, tree in self.modules.items():
            for node in tree.data["body"]:
                if node.kind not in ("fn", "record", "enum", "import"):
                    self.fail(node, "Modules may contain declarations only")
                if node.kind == "fn" and node.data.get("entry"):
                    if (
                        mid != "main"
                        or node.data["params"]
                        or node.data["type"] != "unit"
                        or node.data.get("type_params")
                    ):
                        self.fail(
                            node,
                            "Entry must be a parameterless unit function in the root module",
                        )
                    entries.append(node)
        if len(entries) > 1 or self.require_entry and len(entries) != 1:
            self.fail(
                self.modules["main"], "Program requires exactly one entry function"
            )

        for mid, tree in self.modules.items():
            self.mid, self.scopes, self.next_slot = mid, [{}], 0
            for declaration in tree.data["body"]:
                if declaration.kind in ("record", "enum"):
                    parameters = [
                        str(p["name"]) for p in declaration.data.get("type_params", [])
                    ]
                    if len(parameters) != len(set(parameters)) or len(parameters) > 32:
                        self.fail(
                            declaration, "Invalid or duplicate type parameters", "type"
                        )
                    self.type_parameters = set(parameters)
                    if declaration.kind == "enum":
                        variants = declaration.data["variants"]
                        tags = [v.data["name"] for v in variants]
                        if (
                            not 1 <= len(tags) <= 256
                            or len(tags) != len(set(tags))
                            or any(
                                not isinstance(t, str)
                                or not t.isidentifier()
                                or len(t) > 128
                                for t in tags
                            )
                        ):
                            self.fail(
                                declaration,
                                "Invalid or duplicate enum variants",
                                "type",
                            )
                        groups = [v.data["params"] for v in variants]
                    else:
                        groups = [declaration.data["fields"]]
                    for fields in groups:
                        names = [field["name"] for field in fields]
                        if (
                            len(fields) > 1024
                            or len(names) != len(set(names))
                            or any(
                                not isinstance(name, str)
                                or not name.isidentifier()
                                or len(name) > 128
                                for name in names
                            )
                        ):
                            self.fail(
                                declaration,
                                "Invalid or duplicate payload fields",
                                "type",
                            )
                        for field in fields:
                            self.check_type(field["type"], declaration)
                    self.type_parameters = set()
                    if not parameters:
                        (
                            self.enum_schema
                            if declaration.kind == "enum"
                            else self.record_schema
                        )(
                            f"@{0 if mid == 'main' else mid}:{declaration.data['name']}",
                            declaration,
                        )
            self.block(tree.data["body"])
            tree.data["_locals"] = self.next_slot
            for n in tree.data["body"]:
                if n.kind != "fn":
                    continue
                self.check_function(n)
                if not n.data.get("type_params"):
                    self.function_instances[f"{mid}:{n.data['name']}"] = n
        checked = {key for key in self.function_instances if "<" not in key}
        while pending := set(self.function_instances) - checked:
            for key in sorted(pending):
                checked.add(key)
                module = key.split(":", 1)[0]
                self.mid = "main" if module == "main" else int(module)
                self.check_function(self.function_instances[key])
        self.modules["main"].data["_function_instances"] = {
            **self.function_instances,
            **self.lambda_instances,
        }
        expanded = set()
        while (
            pending := (set(self.record_instances) | set(self.enum_instances))
            - expanded
        ):
            for typ in sorted(pending):
                expanded.add(typ)
                value = parse_type(typ)
                nominal = value.name
                self.type_access = {
                    t.name
                    for argument in value.args
                    for t in argument.walk()
                    if t.kind == "nominal"
                }
                module = nominal[1:].split(":")[0] if nominal.startswith("@") else "0"
                self.mid = "main" if module == "0" else int(module)
                declaration = (
                    self.records.get(nominal)
                    or self.enums.get(nominal)
                    or self.modules["main"]
                )
                groups = (
                    self.enum_instances[typ].values()
                    if typ in self.enum_instances
                    else [self.record_instances[typ]]
                )
                for group in groups:
                    for field_type in group.values():
                        self.check_type(field_type, declaration)
        self.modules["main"].data["_record_instances"] = self.record_instances
        self.modules["main"].data["_enum_instances"] = self.enum_instances
        return self.modules
