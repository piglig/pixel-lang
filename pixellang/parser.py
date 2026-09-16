"""Spatial AST construction from region topology and semantic tokens."""

import math
import struct

from .model import Node, PixelError, Span

PRECEDENCE = {
    "or": 1,
    "and": 2,
    "==": 3,
    "!=": 3,
    "<": 4,
    "<=": 4,
    ">": 4,
    ">=": 4,
    "+": 5,
    "-": 5,
    "*": 6,
    "/": 6,
    "%": 6,
}


class RowParser:
    def __init__(self, region):
        self.region, self.tokens, self.i = region, region.tokens, 0

    def peek(self, value=None):
        t = self.tokens[self.i] if self.i < len(self.tokens) else None
        return (
            t
            if value is None
            else bool(
                t and t.value == value and t.kind in ("op", "keyword", "punct", "type")
            )
        )

    def pop(self):
        if self.i >= len(self.tokens):
            self.error("Unexpected end of region")
        t = self.tokens[self.i]
        self.i += 1
        return t

    def take(self, value):
        if self.peek(value):
            self.i += 1
            return True
        return False

    def need(self, value):
        if not self.take(value):
            self.error(f"Expected {value}")

    def error(self, message):
        raise PixelError(
            "syntax", message, self.peek().span if self.peek() else self.region.span
        )

    def ident(self):
        t = self.pop()
        if t.kind != "id":
            raise PixelError("syntax", "Expected identifier pixel", t.span)
        return t.value

    def field_name(self):
        token = self.pop()
        if token.kind != "string_head":
            self.error("Field name requires UTF-8 string pixels")
        words = [self.pop() for _ in range((token.value + 1) // 2)]
        if any(w.kind != "word" for w in words):
            self.error("Field name requires data words")
        data = b"".join(w.value.to_bytes(2, "big") for w in words)
        try:
            if token.value % 2 and data[-1] != 0:
                self.error("Invalid field name padding")
            return data[: token.value].decode("utf-8")
        except UnicodeError:
            self.error("Invalid field name UTF-8")

    def typename(self, depth=0):
        if depth > 32:
            self.error("Type nesting limit exceeded")
        t = self.pop()
        if t.kind != "type":
            raise PixelError("syntax", "Expected a type pixel", t.span)
        result = t.value
        if result == "function":
            self.need("(")
            parameters = []
            if not self.take(")"):
                parameters.append(self.typename(depth + 1))
                while self.take(","):
                    parameters.append(self.typename(depth + 1))
                self.need(")")
            self.need("->")
            self.need("(")
            result = (
                "fn(" + ",".join(parameters) + ")->(" + self.typename(depth + 1) + ")"
            )
            self.need(")")
        if result == "record":
            self.need("(")
            module = self.ident()
            self.need(".")
            result = f"@{module}:{self.ident()}"
            if self.take("["):
                args = [self.typename(depth + 1)]
                while self.take(","):
                    args.append(self.typename(depth + 1))
                self.need("]")
                result += "<" + ",".join(args) + ">"
            self.need(")")
        if result == "parameter":
            self.need("(")
            result = "$" + str(self.ident())
            self.need(")")
        if result == "map":
            self.need("(")
            result = self.typename(depth + 1) + "{}"
            self.need(")")
        if result == "Option":
            self.need("(")
            result = "Option<" + self.typename(depth + 1) + ">"
            self.need(")")
        while self.take("["):
            depth += 1
            if depth > 32:
                self.error("Type nesting limit exceeded")
            self.need("]")
            result += "[]"
        return result

    def case_pattern(self):
        if self.take("otherwise"):
            return {"enum_type": None, "variant": None, "params": []}
        typ = self.typename()
        variant = self.field_name()
        self.need("(")
        params = []
        if not self.peek(")"):
            params.append({"name": self.ident()})
            while self.take(","):
                params.append({"name": self.ident()})
        self.need(")")
        return {"enum_type": typ, "variant": variant, "params": params}

    def expr(self, minimum=0):
        t = self.pop()
        if t.kind == "keyword" and t.value == "variant":
            self.need("(")
            typ = self.typename()
            self.need(",")
            variant = self.field_name()
            args = []
            while self.take(","):
                args.append(self.expr())
            self.need(")")
            left = Node(
                "enum_value",
                {"enum_type": typ, "variant": variant, "args": args},
                t.span,
            )
        elif t.kind == "keyword" and t.value == "match":
            self.need("(")
            subject = self.expr()
            self.need(")")
            self.need("{")
            cases = []
            if not self.peek("}"):
                while True:
                    self.need("case")
                    pattern = self.case_pattern()
                    self.need("=>")
                    cases.append(
                        Node("case", {**pattern, "value": self.expr()}, t.span)
                    )
                    if not self.take(","):
                        break
            self.need("}")
            left = Node("match", {"value": subject, "cases": cases}, t.span)
        elif t.kind in ("int", "bool"):
            left = Node("literal", {"value": t.value, "type": t.kind}, t.span)
        elif t.kind in ("string_head", "integer_head", "float_head"):
            if t.kind in ("integer_head", "float_head") and t.value != 0:
                raise PixelError("encoding", "Reserved integer header payload", t.span)
            count = (t.value + 1) // 2 if t.kind == "string_head" else 4
            words = [self.pop() for _ in range(count)]
            if any(w.kind != "word" for w in words):
                raise PixelError(
                    "syntax", "Literal header requires data-word pixels", t.span
                )
            data = b"".join(w.value.to_bytes(2, "big") for w in words)
            try:
                if t.kind == "string_head":
                    if t.value % 2 and data[-1] != 0:
                        raise ValueError("Nonzero string padding")
                    value, typ = data[: t.value].decode("utf-8"), "str"
                elif t.kind == "float_head":
                    value, typ = struct.unpack(">d", data)[0], "float64"
                    if not math.isfinite(value):
                        raise PixelError(
                            "encoding", "Float64 literal must be finite", t.span
                        )
                else:
                    value, typ = int.from_bytes(data, "big", signed=True), "int"
            except (ValueError, UnicodeError) as e:
                raise PixelError(
                    "encoding", "Invalid UTF-8 string or padding", t.span
                ) from e
            left = Node(
                "literal",
                {"value": value, "type": typ},
                Span.merge(t.span, *(w.span for w in words)),
            )
        elif t.kind == "keyword" and t.value == "lambda":
            self.need("(")
            identity = self.ident()
            self.need(")")
            left = Node("lambda_ref", {"name": identity}, t.span)
        elif t.kind == "builtin" and t.value == "jsonDecode":
            self.need("[")
            target_type = self.typename()
            self.need("]")
            self.need("(")
            value = self.expr()
            self.need(")")
            left = Node(
                "json_decode",
                {"type": target_type, "value": value},
                Span.merge(t.span, value.span),
            )
        elif t.kind in ("id", "builtin"):
            name, module = t.value, None
            if t.kind == "id" and self.take("."):
                module, name = name, self.ident()
            type_args = []
            if (
                self.peek("[")
                and self.i + 1 < len(self.tokens)
                and self.tokens[self.i + 1].kind == "type"
            ):
                self.pop()
                type_args.append(self.typename())
                while self.take(","):
                    type_args.append(self.typename())
                self.need("]")
            if self.take("("):
                args = []
                if not self.peek(")"):
                    while True:
                        args.append(self.expr())
                        if not self.take(","):
                            break
                self.need(")")
                left = Node(
                    "builtin" if t.kind == "builtin" else "call",
                    {
                        "name": name,
                        "module": module,
                        "args": args,
                        **({"type_args": type_args} if type_args else {}),
                    },
                    Span.merge(t.span, *(a.span for a in args)),
                )
            elif t.kind == "builtin":
                self.error("Expected function arguments")
            elif module is not None or type_args:
                left = Node(
                    "function_value",
                    {"name": name, "module": module, "type_args": type_args},
                    t.span,
                )
            else:
                left = Node("variable", {"name": name}, t.span)
        elif t.kind == "type":
            self.i -= 1
            declared = self.typename()
            self.need("(")
            left = self.expr()
            self.need(")")
            if declared.startswith("@") and not declared.endswith(("[]", "{}")):
                if left.kind != "map" or any(
                    key.kind != "literal" or key.data["type"] != "str"
                    for key in left.data["items"][::2]
                ):
                    self.error("Record constructor requires named fields")
                left = Node(
                    "record_value",
                    {
                        "record_type": declared,
                        "names": [key.data["value"] for key in left.data["items"][::2]],
                        "items": left.data["items"][1::2],
                    },
                    left.span,
                )
            else:
                if (left.kind, declared[-2:]) not in (("array", "[]"), ("map", "{}")):
                    self.error("Typed collection requires a matching literal")
                left.data["_declared_type"] = declared
        elif t.kind == "op" and t.value in ("-", "not"):
            operand = self.expr(7)
            left = Node(
                "unary",
                {"op": t.value, "operand": operand},
                Span.merge(t.span, operand.span),
            )
        elif t.kind == "punct" and t.value == "(":
            left = self.expr()
            self.need(")")
        elif t.kind == "punct" and t.value == "{":
            items = []
            if not self.peek("}"):
                while True:
                    items.append(self.expr())
                    self.need(":")
                    items.append(self.expr())
                    if not self.take(","):
                        break
            self.need("}")
            left = Node(
                "map", {"items": items}, Span.merge(t.span, *(a.span for a in items))
            )
        elif t.kind == "punct" and t.value == "[":
            items = []
            if not self.peek("]"):
                while True:
                    items.append(self.expr())
                    if not self.take(","):
                        break
            self.need("]")
            left = Node(
                "array", {"items": items}, Span.merge(t.span, *(a.span for a in items))
            )
        else:
            raise PixelError(
                "syntax", "Expected a value, call or grouped expression", t.span
            )
        while self.peek("[") or self.peek("@") or self.peek("("):
            if self.take("("):
                args = []
                if not self.peek(")"):
                    args.append(self.expr())
                    while self.take(","):
                        args.append(self.expr())
                self.need(")")
                left = Node(
                    "invoke",
                    {"target": left, "args": args},
                    Span.merge(left.span, self.tokens[self.i - 1].span),
                )
            elif self.peek("@"):
                marker = self.pop()
                first = self.i
                name = self.field_name()
                span = Span.merge(
                    left.span,
                    marker.span,
                    *(token.span for token in self.tokens[first : self.i]),
                )
                left = Node("field", {"target": left, "name": name}, span)
            else:
                self.need("[")
                index = self.expr()
                self.need("]")
                left = Node(
                    "index",
                    {"target": left, "index": index},
                    Span.merge(left.span, index.span),
                )
        while (
            self.peek()
            and self.peek().kind == "op"
            and PRECEDENCE.get(self.peek().value, -1) >= minimum
        ):
            op = self.pop()
            right = self.expr(PRECEDENCE[op.value] + 1)
            left = Node(
                "binary",
                {"op": op.value, "left": left, "right": right},
                Span.merge(left.span, op.span, right.span),
            )
        return left

    def statement(self):
        exported = self.take("export")
        entry = self.take("entry")
        if entry and not self.peek("fn"):
            self.error("Entry marker requires a function")
        if (
            exported
            and not self.peek("fn")
            and not self.peek("record")
            and not self.peek("enum")
        ):
            self.error("Only functions may be exported")
        mutable = self.peek("var")
        if self.take("let") or self.take("var"):
            name = self.ident()
            self.need(":")
            typ = self.typename()
            self.need("=")
            n = Node(
                "let",
                {"name": name, "type": typ, "mutable": mutable, "value": self.expr()},
            )
        elif self.take("set"):
            target = self.expr()
            assignment = self.pop().value
            if assignment not in ("=", "+=", "-=", "*=", "/=", "%="):
                self.error("Expected assignment operator")
            if target.kind == "variable":
                n = Node("set", {"name": target.data["name"], "value": self.expr()})
            elif target.kind in ("index", "field"):
                n = Node(target.kind + "_set", {**target.data, "value": self.expr()})
            else:
                self.error("Assignment target must be a variable or array index")
            if assignment != "=":
                n.data["update"] = assignment[0]
        elif self.peek("break") or self.peek("continue"):
            n = Node(self.pop().value, {})
        elif self.take("for"):
            name = self.ident()
            extra = {"value_name": self.ident()} if self.take(",") else {}
            self.need("in")
            n = Node("for", {"name": name, **extra, "value": self.expr(), "body": []})
        elif self.take("try"):
            n = Node("try", {"body": [], "otherwise": [], "name": None})
        elif self.take("catch"):
            n = Node("catch", {"name": self.ident(), "body": []})
        elif self.take("if"):
            n = Node("if", {"condition": self.expr(), "body": [], "otherwise": []})
        elif self.take("while"):
            n = Node("while", {"condition": self.expr(), "body": []})
        elif self.take("else"):
            n = Node("else", {"body": []})
        elif self.take("match"):
            n = Node("match_stmt", {"value": self.expr(), "cases": []})
        elif self.take("case"):
            n = Node("case", {**self.case_pattern(), "body": []})
        elif self.peek("record") or self.peek("enum"):
            kind = self.pop().value
            name = self.ident()
            type_params = []
            if self.take("["):
                type_params.append({"name": self.ident()})
                while self.take(","):
                    type_params.append({"name": self.ident()})
                self.need("]")
            self.need("(")
            fields, variants = [], []
            if not self.peek(")"):
                while True:
                    field_name = self.field_name()
                    if kind == "record":
                        self.need(":")
                        fields.append({"name": field_name, "type": self.typename()})
                    else:
                        self.need("(")
                        params = []
                        if not self.peek(")"):
                            while True:
                                name_field = self.field_name()
                                self.need(":")
                                params.append(
                                    {"name": name_field, "type": self.typename()}
                                )
                                if not self.take(","):
                                    break
                        self.need(")")
                        variants.append(
                            Node(
                                "variant_decl",
                                {"name": field_name, "params": params},
                                self.region.span,
                            )
                        )
                    if not self.take(","):
                        break
            self.need(")")
            n = Node(
                kind,
                {
                    "name": name,
                    **(
                        {"fields": fields}
                        if kind == "record"
                        else {"variants": variants, "type_params": type_params}
                    ),
                    "export": exported,
                    **({"type_params": type_params} if type_params else {}),
                },
            )
        elif self.peek("fn") or self.peek("lambda_body"):
            function_kind = self.pop().value
            name = self.ident()
            type_params = []
            if self.take("["):
                type_params.append({"name": self.ident()})
                while self.take(","):
                    type_params.append({"name": self.ident()})
                self.need("]")
            self.need("(")
            params = []
            if not self.peek(")"):
                while True:
                    param = self.ident()
                    self.need(":")
                    params.append({"name": param, "type": self.typename()})
                    if not self.take(","):
                        break
            self.need(")")
            self.need("->")
            typ = self.typename()
            n = Node(
                function_kind,
                {
                    "name": name,
                    "params": params,
                    **({"type_params": type_params} if type_params else {}),
                    "entry": entry,
                    "type": typ,
                    "export": exported,
                    "body": [],
                },
            )
        elif self.take("return"):
            n = Node("return", {"value": self.expr() if self.peek() else None})
        elif self.take("print"):
            n = Node("print", {"value": self.expr()})
        elif self.take("do"):
            n = Node("expr", {"value": self.expr()})
        elif self.take("import"):
            n = Node("import", {"module": self.ident()})
        else:
            n = Node("print", {"value": self.expr()})
        if self.peek():
            self.error("Unexpected token; one statement is allowed per row")
        n.span = self.region.span
        n.data["_region"] = self.region.id
        return n


def parse(regions):
    children = {}
    for r in regions:
        children.setdefault(r.parent, []).append(r)

    def block(parent, depth=0):
        if depth > 128:
            raise PixelError(
                "syntax", "Block nesting exceeds 128", regions[parent].span
            )
        nodes = []
        for r in children.get(parent, []):
            n = RowParser(r).statement()
            if n.kind in (
                "if",
                "while",
                "fn",
                "lambda_body",
                "else",
                "try",
                "catch",
                "for",
                "match_stmt",
                "case",
            ):
                n.data["body"] = block(r.id, depth + 1)
            if n.kind == "match_stmt":
                n.data["cases"] = n.data.pop("body")
                if any(case.kind != "case" for case in n.data["cases"]):
                    raise PixelError("syntax", "Match children must be cases", n.span)
            if n.kind == "catch":
                if (
                    not nodes
                    or nodes[-1].kind != "try"
                    or nodes[-1].data.get("_catch_seen")
                ):
                    raise PixelError(
                        "syntax", "Catch must follow a try in the same scope", n.span
                    )
                nodes[-1].data["_catch_seen"] = True
                nodes[-1].data["name"] = n.data["name"]
                nodes[-1].data["otherwise"] = n.data["body"]
            elif n.kind == "else":
                if (
                    not nodes
                    or nodes[-1].kind != "if"
                    or nodes[-1].data.get("_else_span")
                ):
                    raise PixelError(
                        "syntax", "Else must follow an if in the same scope", n.span
                    )
                nodes[-1].data["otherwise"] = n.data["body"]
                nodes[-1].data["_else_span"] = n.span
            else:
                nodes.append(n)
        for node in nodes:
            if node.kind == "try" and not node.data.get("_catch_seen"):
                raise PixelError("syntax", "Try requires a catch block", node.span)
        return nodes

    nodes = block(None)
    definitions = {}
    for node in nodes:
        if node.kind == "lambda_body":
            key = node.data["name"]
            if (
                key in definitions
                or node.data.get("type_params")
                or node.data.get("entry")
                or node.data.get("export")
            ):
                raise PixelError(
                    "syntax", "Invalid or duplicate lambda body", node.span
                )
            definitions[key] = node
    used = set()

    def restore(node, depth=0):
        if depth > 128:
            raise PixelError("syntax", "Lambda nesting limit exceeded", node.span)
        if node.kind == "lambda_ref":
            key = node.data["name"]
            if key not in definitions or key in used:
                raise PixelError("syntax", "Missing or reused lambda body", node.span)
            used.add(key)
            body = definitions[key]
            node.kind = "lambda"
            node.data = {k: body.data[k] for k in ("params", "type", "body")}
        for value in node.data.values():
            if isinstance(value, Node):
                restore(value, depth + 1)
            elif isinstance(value, list):
                for child in value:
                    if isinstance(child, Node):
                        restore(child, depth + 1)

    roots = [node for node in nodes if node.kind != "lambda_body"]
    for node in roots:
        restore(node)
    if used != set(definitions):
        raise PixelError("syntax", "Unreferenced lambda body")
    return Node("program", {"body": roots}, Span.merge(*(r.span for r in regions)))
