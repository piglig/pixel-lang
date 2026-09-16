"""Human-facing PixelLang syntax. Parses source, never executes host code."""

import json
import math
import re
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import PurePosixPath

from .model import Node, PixelError, TextSpan
from .parser import PRECEDENCE
from .typesys import BUILTINS


@dataclass(frozen=True)
class Lexeme:
    kind: str
    value: object
    start: int
    end: int


class Source:
    def __init__(self, text, name):
        if not isinstance(text, str) or len(text) > 1_000_000:
            raise PixelError(
                "text", "Source must be text of at most one million characters"
            )
        self.text, self.name = text, name
        self.lines = [0] + [i + 1 for i, c in enumerate(text) if c == "\n"]

    def span(self, start, end):
        def point(i):
            line = bisect_right(self.lines, i) - 1
            return (i - self.lines[line], line)

        return TextSpan(self.name, (point(start), point(end)), start, end)

    def error(self, message, start, end=None):
        raise PixelError(
            "syntax", message, self.span(start, start + 1 if end is None else end)
        )


def lex(source):
    text = source.text
    tokens = []
    i = 0
    while i < len(text):
        start = i
        c = text[i]
        if c in " \t\r":
            i += 1
            continue
        if c == "\n":
            tokens.append(Lexeme("separator", "\n", i, i + 1))
            i += 1
            continue
        if text.startswith("//", i):
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                source.error("Unterminated block comment", i, len(text))
            # A multiline comment preserves a statement boundary.
            if "\n" in text[i : end + 2]:
                tokens.append(Lexeme("separator", "\n", i, end + 2))
            i = end + 2
            continue
        if c == '"':
            i += 1
            while i < len(text):
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == '"':
                    break
                if text[i] == "\n":
                    source.error("Use \\n inside a quoted string", start, i)
                i += 1
            if i >= len(text):
                source.error("Unterminated string", start, len(text))
            i += 1
            try:
                value = json.loads(text[start:i])
                value.encode("utf-8")
            except (ValueError, UnicodeError):
                source.error("Invalid string escape or Unicode scalar", start, i)
            tokens.append(Lexeme("str", value, start, i))
            continue
        if c.isdigit() and c.isascii():
            i += 1
            while i < len(text) and (
                text[i].isdigit() and text[i].isascii() or text[i] == "_"
            ):
                i += 1
            raw = text[start:i]
            if (
                i < len(text)
                and text[i] == "."
                and i + 1 < len(text)
                and text[i + 1].isascii()
                and text[i + 1].isdigit()
            ):
                i += 1
                while i < len(text) and (text[i] in "0123456789_"):
                    i += 1
            if i < len(text) and text[i] in "eE":
                i += 1
                if i < len(text) and text[i] in "+-":
                    i += 1
                while i < len(text) and text[i] in "0123456789_":
                    i += 1
            number = text[start:i]
            if number != raw:
                digits = r"[0-9]+(?:_[0-9]+)*"
                if not re.fullmatch(
                    digits + r"(?:\." + digits + r")?(?:[eE][+-]?" + digits + r")?",
                    number,
                ):
                    source.error("Invalid float64 literal", start, i)
                value = float(number.replace("_", ""))
                if not math.isfinite(value):
                    source.error("Float64 literal exceeds finite range", start, i)
                tokens.append(Lexeme("float64", value, start, i))
                continue
            if not re.fullmatch(r"[0-9]+(?:_[0-9]+)*", raw):
                source.error("Invalid integer separator", start, i)
            if len(raw.replace("_", "")) > 20:
                source.error("Integer literal exceeds signed 64-bit range", start, i)
            tokens.append(Lexeme("int", int(raw.replace("_", "")), start, i))
            continue
        if c == "_" or c.isalpha():
            i += 1
            while i < len(text) and (text[i] == "_" or text[i].isalnum()):
                i += 1
            tokens.append(Lexeme("name", text[start:i], start, i))
            continue
        pair = text[i : i + 2]
        if pair in (
            "=>",
            "->",
            ":=",
            "==",
            "!=",
            "<=",
            ">=",
            "&&",
            "||",
            "+=",
            "-=",
            "*=",
            "/=",
            "%=",
            "++",
            "--",
        ):
            tokens.append(Lexeme("symbol", pair, i, i + 2))
            i += 2
            continue
        if c in "(){}[],:;.+-*/%<>=!":
            tokens.append(Lexeme("symbol", c, i, i + 1))
            i += 1
            continue
        source.error(f"Unexpected character {c!r}", i)
    tokens.append(Lexeme("eof", "<eof>", len(text), len(text)))
    return tokens


RESERVED = {
    "enum",
    "match",
    "Option",
    "Some",
    "None",
    "try",
    "catch",
    "json",
    "Error",
    "record",
    "new",
    "map",
    "fn",
    "let",
    "as",
    "func",
    "var",
    "if",
    "else",
    "for",
    "in",
    "while",
    "break",
    "continue",
    "return",
    "import",
    "export",
    "true",
    "false",
    "int",
    "bool",
    "string",
    "print",
    "println",
    "and",
    "or",
    "not",
} | set(BUILTINS)


class TextParser:
    def __init__(self, text, name="main.pxl", tolerant=False):
        self.source = Source(text, name)
        self.type_parameters = set()
        self.tolerant, self.errors = tolerant, []
        lexical_text = text
        while True:
            try:
                self.tokens = lex(Source(lexical_text, name))
                break
            except PixelError as error:
                if not tolerant or len(self.errors) >= 64:
                    raise
                self.errors.append(error)
                start, end = error.span.start, error.span.end
                # Keep offsets and line endings stable; do not invent literal values.
                end = max(start + 1, end)
                if (
                    "string" in error.message.lower()
                    or "quoted" in error.message.lower()
                ):
                    newline = lexical_text.find("\n", start)
                    end = len(text) if newline < 0 else newline
                masked = "".join(
                    c if c in "\r\n" else " " for c in lexical_text[start:end]
                )
                changed = lexical_text[:start] + masked + lexical_text[end:]
                if changed == lexical_text:
                    raise
                lexical_text = changed
        self.i = 0
        self.depth = 0
        self.type_references = []
        self.incomplete_type_references = []
        self.enum_names = {"Option"} | {
            self.tokens[i + 1].value
            for i, t in enumerate(self.tokens[:-1])
            if t.kind == "name" and t.value == "enum"
        }
        self.nominal_type_names = {
            self.tokens[i + 1].value
            for i, t in enumerate(self.tokens[:-1])
            if t.kind == "name" and t.value in ("record", "enum")
        }
        self.function_names = {
            self.tokens[i + 1].value
            for i, t in enumerate(self.tokens[:-1])
            if t.kind == "name"
            and t.value == "fn"
            and self.tokens[i + 1].kind == "name"
        }
        self.module_aliases = set()
        for i, t in enumerate(self.tokens[:-1]):
            if (
                t.kind == "name"
                and t.value == "import"
                and self.tokens[i + 1].kind == "str"
            ):
                alias = PurePosixPath(self.tokens[i + 1].value).stem
                if i + 3 < len(self.tokens) and self.tokens[i + 2].value == "as":
                    alias = self.tokens[i + 3].value
                self.module_aliases.add(alias)

    def peek(self, value=None):
        t = self.tokens[self.i]
        return (
            t
            if value is None
            else t.kind not in ("str", "int", "float64") and t.value == value
        )

    def pop(self):
        t = self.peek()
        if t.kind == "eof":
            self.error("Unexpected end of file")
        self.i += 1
        return t

    def take(self, value):
        if self.peek(value):
            return self.pop()
        return None

    def need(self, value):
        t = self.take(value)
        if not t:
            if self.tolerant and value == "}" and self.peek().kind == "eof":
                self.errors.append(
                    PixelError(
                        "syntax",
                        "Expected closing brace",
                        self.source.span(len(self.source.text), len(self.source.text)),
                    )
                )
                return self.peek()
            self.error(f"Expected {value!r}")
        return t

    def error(self, message):
        t = self.peek()
        self.source.error(message, t.start, t.end)

    def separators(self):
        while self.peek("\n") or self.peek(";"):
            self.i += 1

    def nl(self):
        while self.peek("\n"):
            self.i += 1

    def name(self, module_alias=False):
        t = self.pop()
        if (
            t.kind != "name"
            or t.value in RESERVED
            and not (module_alias and t.value == "json")
        ):
            self.source.error("Expected a non-reserved identifier", t.start, t.end)
        self.last_name_span = self.source.span(t.start, t.end)
        return t.value

    def node(self, kind, data, start, end=None):
        return Node(
            kind,
            data,
            self.source.span(
                start, self.tokens[self.i - 1].end if end is None else end
            ),
        )

    def typename(self):
        if self.take("fn"):
            self.depth += 1
            if self.depth > 32:
                self.error("Type nesting limit exceeded")
            self.need("(")
            parameters = []
            if not self.take(")"):
                parameters.append(self.typename())
                while self.take(","):
                    parameters.append(self.typename())
                self.need(")")
            result = "unit"
            if self.take("->"):
                result = self.typename()
            self.depth -= 1
            return "fn(" + ",".join(parameters) + ")->(" + result + ")"
        if self.take("Option"):
            self.need("[")
            self.depth += 1
            if self.depth > 32:
                self.error("Type nesting limit exceeded")
            value = self.typename()
            self.need("]")
            self.depth -= 1
            return "Option<" + value + ">"
        if self.take("map"):
            self.depth += 1
            if self.depth > 32:
                self.error("Collection type nesting limit exceeded")
            self.need("[")
            element = self.typename()
            self.need("]")
            self.depth -= 1
            return element + "{}"
        if self.take("["):
            self.depth += 1
            if self.depth > 32:
                self.error("Array type nesting limit exceeded")
            element = self.typename()
            self.need("]")
            self.depth -= 1
            return element + "[]"
        t = self.pop()
        if t.value in self.type_parameters:
            self.type_references.append(
                {
                    "name": "$" + t.value,
                    "owner": self.type_parameter_owner,
                    "owner_kind": self.type_parameter_kind,
                    "span": self.source.span(t.start, t.end),
                    "module_span": None,
                }
            )
            return "$" + t.value
        if t.value not in ("int", "float64", "bool", "string", "json", "Error"):
            if t.kind != "name" or t.value in RESERVED:
                self.source.error("Expected a value type", t.start, t.end)
            name = t.value
            name_span = self.source.span(t.start, t.end)
            module_span = None
            if self.take("."):
                module_span = name_span
                name += "." + self.name()
                name_span = self.last_name_span
            self.type_references.append(
                {"name": name, "span": name_span, "module_span": module_span}
            )
            args = []
            if self.take("["):
                self.depth += 1
                if self.depth > 32:
                    self.error("Type nesting limit exceeded")
                if self.peek("]"):
                    self.error("Type arguments cannot be empty")
                while True:
                    args.append(self.typename())
                    if not self.take(","):
                        break
                self.need("]")
                self.depth -= 1
            return "@" + name + ("<" + ",".join(args) + ">" if args else "")
        return {"string": "str"}.get(t.value, t.value)

    def type_parameters_decl(self, owner, kind):
        self.type_parameter_owner, self.type_parameter_kind = owner, kind
        params = []
        if self.take("["):
            self.nl()
            while True:
                name = self.name()
                if name in self.type_parameters:
                    self.error("Duplicate type parameter")
                self.type_parameters.add(name)
                params.append({"name": name, "_name_span": self.last_name_span})
                self.nl()
                if not self.take(","):
                    break
                self.nl()
            self.need("]")
        return params

    def call_ahead(self, pos):
        if self.tokens[pos].value == "(":
            return True
        if self.tokens[pos].value != "[":
            return False
        depth = 0
        while pos < len(self.tokens):
            value = self.tokens[pos].value
            depth += (value == "[") - (value == "]")
            pos += 1
            if depth == 0:
                break
        return pos < len(self.tokens) and self.tokens[pos].value == "("

    def constructor_ahead(self):
        pos = self.i
        if self.tokens[pos].value == ".":
            pos += 2
        if pos < len(self.tokens) and self.tokens[pos].value == "[":
            depth = 0
            while pos < len(self.tokens):
                value = self.tokens[pos].value
                depth += (value == "[") - (value == "]")
                pos += 1
                if depth == 0:
                    break
        return pos < len(self.tokens) and self.tokens[pos].value == "{"

    def variant_name(self):
        token = self.pop()
        if token.kind != "name" or token.value in RESERVED - {"Some", "None"}:
            self.error("Expected variant name")
        return token.value, self.source.span(token.start, token.end)

    def enum_ahead(self, name):
        pos = self.i
        if name in self.enum_names:
            return self.peek(".") or self.peek("[")
        if name not in self.module_aliases or not self.peek("."):
            return False
        pos += 2
        if pos < len(self.tokens) and self.tokens[pos].value == "[":
            level = 0
            while pos < len(self.tokens):
                value = self.tokens[pos].value
                level += (value == "[") - (value == "]")
                pos += 1
                if level == 0:
                    break
        return pos < len(self.tokens) and self.tokens[pos].value == "."

    def enum_reference(self):
        token = self.pop()
        if token.value in ("Some", "None"):
            return "Option", token.value, self.source.span(token.start, token.end)
        name = token.value
        name_span = self.source.span(token.start, token.end)
        module_span = None
        if name in self.module_aliases:
            self.need(".")
            module_span = name_span
            name += "." + self.name()
            name_span = self.last_name_span
        if name != "Option":
            self.type_references.append(
                {"name": name, "span": name_span, "module_span": module_span}
            )
        typ = "Option" if name == "Option" else "@" + name
        if self.take("["):
            args = [self.typename()]
            while self.take(","):
                args.append(self.typename())
            self.need("]")
            typ += "<" + ",".join(args) + ">"
        self.need(".")
        variant, span = self.variant_name()
        return typ, variant, span

    def match_node(self, statement, start):
        subject = self.expression(allow_record=False)
        self.need("{")
        self.separators()
        cases = []
        while not self.peek("}"):
            case_start = self.peek().start
            params = []
            if (
                self.tolerant
                and self.peek().kind == "name"
                and self.tokens[self.i + 1].value in ("}", "<eof>", "\n")
            ):
                token = self.pop()
                typ, variant, span = (
                    None,
                    token.value,
                    self.source.span(token.start, token.end),
                )
            elif self.take("_"):
                typ, variant, span = None, None, None
            else:
                typ, variant, span = self.enum_reference()
                if self.take("("):
                    if not self.peek(")"):
                        while True:
                            name = self.name()
                            params.append(
                                {"name": name, "_name_span": self.last_name_span}
                            )
                            if not self.take(","):
                                break
                    self.need(")")
            self.nl()
            if self.tolerant and (self.peek("}") or self.peek().kind == "eof"):
                self.errors.append(
                    PixelError(
                        "syntax", "Incomplete match arm; expected => and body", span
                    )
                )
                branch = {
                    "_incomplete": True,
                    **(
                        {"body": []}
                        if statement
                        else {
                            "value": self.node(
                                "literal", {"value": 0, "type": "int"}, case_start
                            )
                        }
                    ),
                }
            else:
                self.need("=>")
                branch = (
                    {"body": self.block()}
                    if statement
                    else {"value": self.expression()}
                )
            cases.append(
                self.node(
                    "case",
                    {
                        "enum_type": typ,
                        "variant": variant,
                        "_variant_span": span,
                        "params": params,
                        **branch,
                    },
                    case_start,
                )
            )
            self.take(",")
            self.separators()
        self.need("}")
        return self.node(
            "match_stmt" if statement else "match",
            {"value": subject, "cases": cases},
            start,
        )

    def expression(self, minimum=0, allow_record=True):
        self.depth += 1
        if self.depth > 128:
            self.error("Expression nesting exceeds 128")
        t = self.pop()
        start = t.start
        if t.value == "fn" and t.kind == "name":
            self.need("(")
            self.nl()
            params = []
            if not self.peek(")"):
                while True:
                    name = self.name()
                    span = self.last_name_span
                    annotation = self.typename() if self.take(":") else None
                    params.append(
                        {"name": name, "type": annotation, "_name_span": span}
                    )
                    self.nl()
                    if not self.take(","):
                        break
                    self.nl()
            self.need(")")
            result = self.typename() if self.take("->") else None
            if self.take("="):
                value = self.expression()
                body = [self.node("return", {"value": value}, value.span.start)]
            else:
                body = self.block()
            left = self.node(
                "lambda", {"params": params, "type": result, "body": body}, start
            )
        elif t.value == "match" and t.kind == "name":
            left = self.match_node(False, start)
        elif t.kind == "name" and (
            t.value in ("Some", "None") or self.enum_ahead(t.value)
        ):
            self.i -= 1
            typ, variant, span = self.enum_reference()
            args = []
            if self.take("("):
                if not self.peek(")"):
                    while True:
                        args.append(self.expression())
                        if not self.take(","):
                            break
                self.need(")")
            left = self.node(
                "enum_value",
                {
                    "enum_type": typ,
                    "variant": variant,
                    "args": args,
                    "_variant_span": span,
                },
                start,
            )
        elif t.kind in ("int", "str", "float64"):
            left = self.node("literal", {"value": t.value, "type": t.kind}, start)
        elif t.value in ("true", "false"):
            left = self.node(
                "literal", {"value": t.value == "true", "type": "bool"}, start
            )
        elif t.value in ("-", "!"):
            operand = self.expression(7, allow_record=allow_record)
            if (
                t.value == "-"
                and operand.kind == "literal"
                and operand.data["type"] in ("int", "float64")
            ):
                left = self.node(
                    "literal",
                    {"value": -operand.data["value"], "type": operand.data["type"]},
                    start,
                )
            else:
                left = self.node(
                    "unary",
                    {
                        "op": "not" if t.value == "!" else "-",
                        "operand": operand,
                    },
                    start,
                )
        elif t.value == "(":
            self.nl()
            left = self.expression()
            self.nl()
            self.need(")")
        elif (
            allow_record
            and t.kind == "name"
            and t.value not in RESERVED
            and self.constructor_ahead()
        ):
            self.i -= 1
            declared = self.typename()
            if not declared.startswith("@") or declared.endswith(("[]", "{}")):
                self.error("Constructor requires a record type")
            self.need("{")
            self.nl()
            names, items, field_spans = [], [], []
            if not self.peek("}"):
                while True:
                    names.append(self.name())
                    field_spans.append(self.last_name_span)
                    if self.take(":"):
                        items.append(self.expression())
                    else:
                        span = self.last_name_span
                        items.append(
                            Node(
                                "variable",
                                {"name": names[-1], "_name_span": span},
                                span,
                            )
                        )
                    self.nl()
                    if not self.take(","):
                        break
                    self.nl()
                    if self.peek("}"):
                        break
            self.need("}")
            left = self.node(
                "record_value",
                {
                    "record_type": declared,
                    "names": names,
                    "items": items,
                    "_field_spans": field_spans,
                },
                start,
            )
        elif t.value == "jsonDecode":
            self.need("[")
            target_type = self.typename()
            self.need("]")
            self.need("(")
            value = self.expression()
            self.need(")")
            left = self.node(
                "json_decode", {"type": target_type, "value": value}, start
            )
        elif t.value == "map":
            self.need("[")
            declared = self.typename() + "{}"
            self.need("]")
            self.need("{")
            items = []
            self.nl()
            if not self.peek("}"):
                while True:
                    key = self.expression()
                    self.need(":")
                    value = self.expression()
                    items.extend([key, value])
                    self.nl()
                    if not self.take(","):
                        break
                    self.nl()
                    if self.peek("}"):
                        break
            self.need("}")
            left = self.node("map", {"items": items, "_declared_type": declared}, start)
        elif t.value == "[":
            close = None if self.take("]") else "]"
            items = []
            if close:
                self.nl()
                if not self.peek(close):
                    while True:
                        items.append(self.expression())
                        self.nl()
                        if not self.take(","):
                            break
                        self.nl()
                        if self.peek(close):
                            break
                self.need(close)
            left = self.node("array", {"items": items}, start)
        elif t.kind == "name" and (
            t.value not in RESERVED
            or t.value in BUILTINS
            or t.value == "json"
            and self.peek(".")
        ):
            name, module = t.value, None
            name_span = self.source.span(t.start, t.end)
            module_span = None
            if (
                self.peek(".")
                and name in self.module_aliases
                and self.i + 2 < len(self.tokens)
            ):
                self.pop()
                module = name
                module_span = name_span
                name = self.name()
                name_span = self.last_name_span
            type_args = []
            if (
                self.peek("[")
                and (
                    module is not None
                    or name in self.function_names
                    or name in BUILTINS
                )
                and self.tokens[self.i + 1].value
                in (
                    self.nominal_type_names
                    | self.module_aliases
                    | self.type_parameters
                    | {
                        "int",
                        "float64",
                        "bool",
                        "string",
                        "json",
                        "Error",
                        "Option",
                        "map",
                        "[",
                        "fn",
                    }
                )
            ):
                self.pop()
                self.nl()
                while True:
                    type_args.append(self.typename())
                    self.nl()
                    if not self.take(","):
                        break
                    self.nl()
                self.need("]")
            if self.take("("):
                self.nl()
                args = []
                if not self.peek(")"):
                    while True:
                        args.append(self.expression())
                        self.nl()
                        if not self.take(","):
                            break
                        self.nl()
                        if self.peek(")"):
                            break
                self.need(")")
                left = self.node(
                    "builtin" if name in BUILTINS and module is None else "call",
                    {
                        "name": name,
                        "module": module,
                        "args": args,
                        **({"type_args": type_args} if type_args else {}),
                        "_name_span": name_span,
                        "_module_span": module_span,
                    },
                    start,
                )
            elif module is not None or type_args:
                left = self.node(
                    "function_value",
                    {
                        "name": name,
                        "module": module,
                        "type_args": type_args,
                        "_name_span": name_span,
                        "_module_span": module_span,
                    },
                    start,
                )
            elif name in BUILTINS:
                self.error("Builtin name requires arguments")
            else:
                left = self.node(
                    "variable", {"name": name, "_name_span": name_span}, start
                )
        else:
            self.source.error("Expected an expression", start, t.end)
        while self.peek("[") or self.peek(".") or self.peek("("):
            if self.take("("):
                self.nl()
                args = []
                if not self.peek(")"):
                    while True:
                        args.append(self.expression())
                        self.nl()
                        if not self.take(","):
                            break
                        self.nl()
                        if self.peek(")"):
                            break
                self.need(")")
                left = self.node("invoke", {"target": left, "args": args}, start)
            elif self.take("."):
                name = self.name()
                left = self.node(
                    "field",
                    {"target": left, "name": name, "_name_span": self.last_name_span},
                    start,
                )
            else:
                self.need("[")
                self.nl()
                index = self.expression()
                self.nl()
                self.need("]")
                left = self.node("index", {"target": left, "index": index}, start)
        if left.kind == "array" and self.take(":"):
            declared = self.typename()
            if not declared.endswith("[]"):
                self.error("Array annotation requires an array type")
            left.data["_declared_type"] = declared
        while True:
            raw = self.peek().value
            if raw in ("and", "or"):
                break
            op = {"&&": "and", "||": "or"}.get(raw, raw)
            if PRECEDENCE.get(op, -1) < minimum:
                break
            self.pop()
            self.nl()
            right = self.expression(PRECEDENCE[op] + 1, allow_record=allow_record)
            left = self.node("binary", {"op": op, "left": left, "right": right}, start)
        self.depth -= 1
        return left

    def block(self):
        self.need("{")
        self.nl()
        body = self.statements("}")
        self.need("}")
        return body

    def statement(self):
        start = self.peek().start
        exported = bool(self.take("export"))
        if self.take("enum"):
            name = self.name()
            name_span = self.last_name_span
            type_params = self.type_parameters_decl(name, "enum")
            self.need("{")
            self.separators()
            variants = []
            while not self.peek("}"):
                variant, span = self.variant_name()
                params = []
                if self.take("("):
                    if not self.peek(")"):
                        while True:
                            field = self.name()
                            field_span = self.last_name_span
                            self.need(":")
                            params.append(
                                {
                                    "name": field,
                                    "type": self.typename(),
                                    "_name_span": field_span,
                                }
                            )
                            if not self.take(","):
                                break
                    self.need(")")
                variants.append(
                    self.node(
                        "variant_decl",
                        {"name": variant, "params": params, "_name_span": span},
                        span.start,
                    )
                )
                self.take(",")
                self.separators()
            self.need("}")
            self.type_parameters.clear()
            return self.node(
                "enum",
                {
                    "name": name,
                    "_name_span": name_span,
                    "type_params": type_params,
                    "variants": variants,
                    "export": exported,
                },
                start,
            )
        if self.take("match"):
            if exported:
                self.error("Only declarations may be exported")
            return self.match_node(True, start)
        if exported and self.peek().value not in ("fn", "record"):
            self.error("Only functions and records can be exported")
        if self.take("record"):
            name = self.name()
            name_span = self.last_name_span
            type_params = self.type_parameters_decl(name, "record")
            self.need("{")
            self.separators()
            fields = []
            while not self.peek("}"):
                field_name = self.name()
                field_span = self.last_name_span
                self.need(":")
                fields.append(
                    {
                        "name": field_name,
                        "type": self.typename(),
                        "_name_span": field_span,
                    }
                )
                if self.take(","):
                    self.separators()
                elif not self.peek("}"):
                    if not self.peek("\n") and not self.peek(";"):
                        self.error("Expected a field separator")
                    self.separators()
            self.need("}")
            self.type_parameters.clear()
            return self.node(
                "record",
                {
                    "name": name,
                    "_name_span": name_span,
                    "fields": fields,
                    "export": exported,
                    **({"type_params": type_params} if type_params else {}),
                },
                start,
            )
        if self.take("fn"):
            name = self.name()
            name_span = self.last_name_span
            type_params = self.type_parameters_decl(name, "fn")
            self.need("(")
            self.nl()
            params = []
            if not self.peek(")"):
                while True:
                    param = self.name()
                    param_span = self.last_name_span
                    self.need(":")
                    typ = self.typename()
                    params.append(
                        {"name": param, "type": typ, "_name_span": param_span}
                    )
                    self.nl()
                    if not self.take(","):
                        break
                    self.nl()
            self.need(")")
            typ = self.typename() if self.take("->") else "unit"
            if self.take("="):
                if typ == "unit":
                    self.error("An expression function needs a return type: -> type")
                value = self.expression()
                body = [self.node("return", {"value": value}, value.span.start)]
            else:
                body = self.block()
            self.type_parameters.clear()
            return self.node(
                "fn",
                {
                    "name": name,
                    "_name_span": name_span,
                    "params": params,
                    "type": typ,
                    "export": exported,
                    "body": body,
                    **({"type_params": type_params} if type_params else {}),
                },
                start,
            )
        if self.take("import"):
            alias = None
            alias_span = None
            t = self.pop()
            if t.kind != "str":
                self.source.error("Import expects a quoted .pxl path", t.start, t.end)
            path = t.value
            if self.take("as"):
                alias = self.name(module_alias=True)
                alias_span = self.last_name_span
            if not path.endswith(".pxl"):
                self.source.error("Module filename must end in .pxl", t.start, t.end)
            alias = alias or PurePosixPath(path).stem
            if not alias.isidentifier() or alias in RESERVED and alias != "json":
                self.source.error("Module needs a valid alias", start, t.end)
            return self.node(
                "import",
                {
                    "module": path,
                    "_alias": alias,
                    "_alias_span": alias_span,
                    "_path_span": self.source.span(t.start, t.end),
                },
                start,
            )
        mutable = self.peek("var")
        if self.take("var") or self.take("let"):
            name = self.name()
            name_span = self.last_name_span
            typ = self.typename() if self.take(":") else None
            self.need("=")
            return self.node(
                "let",
                {
                    "name": name,
                    "_name_span": name_span,
                    "type": typ,
                    "mutable": mutable,
                    "value": self.expression(),
                },
                start,
            )
        if self.take("try"):
            body = self.block()
            self.nl()
            self.need("catch")
            name = self.name()
            name_span = self.last_name_span
            otherwise = self.block()
            return self.node(
                "try",
                {
                    "body": body,
                    "name": name,
                    "_name_span": name_span,
                    "otherwise": otherwise,
                },
                start,
            )
        if self.take("if"):
            condition = self.expression(allow_record=False)
            body = self.block()
            otherwise = []
            saved = self.i
            self.nl()
            if self.take("else"):
                if self.peek("if"):
                    otherwise = [self.statement()]
                else:
                    otherwise = self.block()
            else:
                self.i = saved
            return self.node(
                "if",
                {"condition": condition, "body": body, "otherwise": otherwise},
                start,
            )
        if self.take("for"):
            name = self.name()
            name_span = self.last_name_span
            extra = {}
            if self.take(","):
                extra["value_name"] = self.name()
                extra["_value_name_span"] = self.last_name_span
            self.need("in")
            value = self.expression(allow_record=False)
            return self.node(
                "for",
                {
                    "name": name,
                    "_name_span": name_span,
                    **extra,
                    "value": value,
                    "body": self.block(),
                },
                start,
            )
        if self.take("while"):
            condition = self.expression(allow_record=False)
            return self.node(
                "while", {"condition": condition, "body": self.block()}, start
            )
        if self.peek("break") or self.peek("continue"):
            return self.node(self.pop().value, {}, start)
        if self.take("return"):
            value = (
                None
                if any(self.peek(token) for token in ("\n", ";", "}", "<eof>"))
                else self.expression()
            )
            return self.node("return", {"value": value}, start)
        if self.take("print"):
            self.need("(")
            self.nl()
            value = self.expression()
            self.nl()
            self.need(")")
            return self.node("print", {"value": value}, start)
        left = self.expression()
        op = self.peek().value
        if op == ":=":
            self.error("Use let or var for declarations")
        if op in ("=", "+=", "-=", "*=", "/=", "%=", "++", "--"):
            self.pop()
            if left.kind not in ("variable", "index", "field"):
                self.error("Assignment needs a variable or array index")
            value = (
                self.node("literal", {"value": 1, "type": "int"}, start)
                if op in ("++", "--")
                else self.expression()
            )
            update = {"update": op[0]} if op != "=" else {}
            if left.kind in ("index", "field"):
                return self.node(
                    left.kind + "_set", {**left.data, "value": value, **update}, start
                )
            return self.node(
                "set",
                {
                    "name": left.data["name"],
                    "_name_span": left.data["_name_span"],
                    "value": value,
                    **update,
                },
                start,
            )
        return self.node("expr", {"value": left}, start)

    def statements(self, end):
        result = []
        self.separators()
        while not self.peek(end):
            if self.peek().kind == "eof":
                if self.tolerant:
                    break
                self.error(f"Expected {end}")
            start, depth, refs = self.i, self.depth, len(self.type_references)
            type_parameters = self.type_parameters.copy()
            try:
                statement = self.statement()
                if not self.peek(end) and not any(
                    self.peek(token) for token in (";", "\n", "<eof>")
                ):
                    self.error("Expected newline or semicolon between statements")
                result.append(statement)
            except PixelError as error:
                if not self.tolerant:
                    raise
                self.errors.append(error)
                self.i, self.depth = start, depth
                self.type_parameters = type_parameters
                self.incomplete_type_references.extend(self.type_references[refs:])
                del self.type_references[refs:]
                braces = 0
                while self.peek().kind != "eof":
                    if braces == 0 and (
                        self.peek("\n") or self.peek(";") or self.peek(end)
                    ):
                        break
                    token = self.pop()
                    if token.value == "{" and token.kind != "str":
                        braces += 1
                    if token.value == "}" and token.kind != "str":
                        braces = max(0, braces - 1)
            self.separators()
        return result

    def parse(self):
        try:
            body = self.statements("<eof>")
            return self.node(
                "program",
                {
                    "body": body,
                    "_type_refs": self.type_references,
                    "_incomplete_type_refs": self.incomplete_type_references,
                    "_language": "0.8",
                    "_syntax_errors": self.errors,
                },
                0,
                len(self.source.text),
            )
        except RecursionError as e:
            raise PixelError(
                "syntax",
                "Program nesting exceeds compiler limit",
                self.source.span(0, 0),
            ) from e


def parse_text(text, name="main.pxl", tolerant=False):
    return TextParser(text, name, tolerant=tolerant).parse()
