"""Canonical semantic serialization and AST → canonical spatial source."""

import json

from .codec import encode_token, new_source
from .model import Node
from .typeexpr import parse_type


def semantic(tree):
    return tree.to_dict(provenance=False)


def fingerprint(tree):
    return json.dumps(semantic(tree), sort_keys=True, separators=(",", ":"))


def encode(tree, annotations=None):
    rows = []
    lambdas, lambda_ids = [], {}

    def discover(node):
        if node.kind == "lambda":
            lambda_ids[id(node)] = len(lambdas)
            lambdas.append(node)
        for key, value in node.data.items():
            if key.startswith("_"):
                continue
            if isinstance(value, Node):
                discover(value)
            elif isinstance(value, list):
                for child in value:
                    if isinstance(child, Node):
                        discover(child)

    discover(tree)

    def tok(k, v, owner):
        return (k, v, owner.span)

    def typ(name, owner):
        if name.startswith("$") and not name.endswith(("[]", "{}")):
            return [
                tok("type", "parameter", owner),
                tok("punct", "(", owner),
                tok("id", int(name[1:]), owner),
                tok("punct", ")", owner),
            ]
        if name.endswith("[]"):
            return typ(name[:-2], owner) + [
                tok("punct", "[", owner),
                tok("punct", "]", owner),
            ]
        if name.endswith("{}"):
            return (
                [tok("type", "map", owner), tok("punct", "(", owner)]
                + typ(name[:-2], owner)
                + [tok("punct", ")", owner)]
            )
        if name.startswith("fn("):
            value = parse_type(name)
            result = [tok("type", "function", owner), tok("punct", "(", owner)]
            for index, parameter in enumerate(value.args[:-1]):
                if index:
                    result.append(tok("punct", ",", owner))
                result += typ(parameter.spelling(), owner)
            return (
                result
                + [
                    tok("punct", ")", owner),
                    tok("punct", "->", owner),
                    tok("punct", "(", owner),
                ]
                + typ(value.args[-1].spelling(), owner)
                + [tok("punct", ")", owner)]
            )
        if name.startswith("@"):
            value = parse_type(name)
            module, identifier = map(int, value.name[1:].split(":"))
            result = [
                tok("type", "record", owner),
                tok("punct", "(", owner),
                tok("id", module, owner),
                tok("punct", ".", owner),
                tok("id", identifier, owner),
            ]
            if value.args:
                result.append(tok("punct", "[", owner))
                for index, argument in enumerate(value.args):
                    if index:
                        result.append(tok("punct", ",", owner))
                    result += typ(argument.spelling(), owner)
                result.append(tok("punct", "]", owner))
            return result + [tok("punct", ")", owner)]
        if name.startswith("Option<"):
            return (
                [tok("type", "Option", owner), tok("punct", "(", owner)]
                + typ(parse_type(name).args[0].spelling(), owner)
                + [tok("punct", ")", owner)]
            )
        return [tok("type", name, owner)]

    def pattern(n):
        d = n.data
        if d["variant"] is None:
            return [tok("keyword", "otherwise", n)]
        result = typ(d["enum_type"], n) + [
            tok("str", d["variant"], n),
            tok("punct", "(", n),
        ]
        for index, parameter in enumerate(d["params"]):
            if index:
                result.append(tok("punct", ",", n))
            result.append(tok("id", parameter["name"], n))
        return result + [tok("punct", ")", n)]

    def expr(n):
        d = n.data
        if n.kind == "literal":
            return [tok(d["type"], d["value"], n)]
        if n.kind == "enum_value":
            result = (
                [tok("keyword", "variant", n), tok("punct", "(", n)]
                + typ(d["enum_type"], n)
                + [tok("punct", ",", n), tok("str", d["variant"], n)]
            )
            for argument in d["args"]:
                result += [tok("punct", ",", n)] + expr(argument)
            return result + [tok("punct", ")", n)]
        if n.kind == "match":
            result = (
                [tok("keyword", "match", n), tok("punct", "(", n)]
                + expr(d["value"])
                + [tok("punct", ")", n), tok("punct", "{", n)]
            )
            for index, case in enumerate(d["cases"]):
                if index:
                    result.append(tok("punct", ",", n))
                result += (
                    [tok("keyword", "case", case)]
                    + pattern(case)
                    + [tok("punct", "=>", case)]
                    + expr(case.data["value"])
                )
            return result + [tok("punct", "}", n)]
        if n.kind == "variable":
            return [tok("id", d["name"], n)]
        if n.kind == "unary":
            return (
                [tok("punct", "(", n), tok("op", d["op"], n)]
                + expr(d["operand"])
                + [tok("punct", ")", n)]
            )
        if n.kind == "binary":
            return (
                [tok("punct", "(", n)]
                + expr(d["left"])
                + [tok("op", d["op"], n)]
                + expr(d["right"])
                + [tok("punct", ")", n)]
            )
        if n.kind == "field":
            return expr(d["target"]) + [tok("punct", "@", n), tok("str", d["name"], n)]
        if n.kind == "record_value":
            tokens = typ(d["record_type"], n) + [
                tok("punct", "(", n),
                tok("punct", "{", n),
            ]
            for i, (name, value) in enumerate(zip(d["names"], d["items"])):
                if i:
                    tokens.append(tok("punct", ",", n))
                tokens += [tok("str", name, n), tok("punct", ":", n)] + expr(value)
            return tokens + [tok("punct", "}", n), tok("punct", ")", n)]
        if n.kind == "index":
            return (
                expr(d["target"])
                + [tok("punct", "[", n)]
                + expr(d["index"])
                + [tok("punct", "]", n)]
            )
        if n.kind == "map":
            tokens = typ(d.get("_type") or d["_declared_type"], n) + [
                tok("punct", "(", n),
                tok("punct", "{", n),
            ]
            for i in range(0, len(d["items"]), 2):
                if i:
                    tokens.append(tok("punct", ",", n))
                tokens += (
                    expr(d["items"][i])
                    + [tok("punct", ":", n)]
                    + expr(d["items"][i + 1])
                )
            return tokens + [tok("punct", "}", n), tok("punct", ")", n)]
        if n.kind == "array":
            declared = d.get("_type") or d.get("_declared_type")
            tokens = (typ(declared, n) + [tok("punct", "(", n)] if declared else []) + [
                tok("punct", "[", n)
            ]
            for i, item in enumerate(d["items"]):
                if i:
                    tokens.append(tok("punct", ",", n))
                tokens += expr(item)
            return (
                tokens
                + [tok("punct", "]", n)]
                + ([tok("punct", ")", n)] if declared else [])
            )
        if n.kind == "json_decode":
            return (
                [tok("builtin", "jsonDecode", n), tok("punct", "[", n)]
                + typ(d["type"], n)
                + [tok("punct", "]", n), tok("punct", "(", n)]
                + expr(d["value"])
                + [tok("punct", ")", n)]
            )
        if n.kind == "lambda":
            return [
                tok("keyword", "lambda", n),
                tok("punct", "(", n),
                tok("id", lambda_ids[id(n)], n),
                tok("punct", ")", n),
            ]
        if n.kind == "invoke":
            result = (
                [tok("punct", "(", n)]
                + expr(d["target"])
                + [tok("punct", ")", n), tok("punct", "(", n)]
            )
            for index, argument in enumerate(d["args"]):
                if index:
                    result.append(tok("punct", ",", n))
                result += expr(argument)
            return result + [tok("punct", ")", n)]
        if n.kind in ("call", "builtin", "function_value"):
            result = (
                []
                if d.get("module") is None
                else [tok("id", d["module"], n), tok("punct", ".", n)]
            )
            result += [
                tok("builtin" if n.kind == "builtin" else "id", d["name"], n),
            ]
            if d.get("type_args"):
                result.append(tok("punct", "[", n))
                for index, argument in enumerate(d["type_args"]):
                    if index:
                        result.append(tok("punct", ",", n))
                    result += typ(argument, n)
                result.append(tok("punct", "]", n))
            if n.kind == "function_value":
                return result
            result.append(tok("punct", "(", n))
            for i, a in enumerate(d["args"]):
                if i:
                    result.append(tok("punct", ",", n))
                result += expr(a)
            return result + [tok("punct", ")", n)]
        raise ValueError(n.kind)

    def block(nodes, depth):
        for n in nodes:
            d, kind = n.data, n.kind
            assignment = d["update"] + "=" if "update" in d else "="
            keyword = {
                "index_set": "set",
                "field_set": "set",
                "expr": "do",
                "match_stmt": "match",
            }.get(kind, kind)
            if kind == "let" and d.get("mutable", False):
                keyword = "var"
            ts = [tok("keyword", keyword, n)]
            if kind == "fn" and d.get("entry"):
                ts.insert(0, tok("keyword", "entry", n))
            if kind == "let":
                ts += (
                    [tok("id", d["name"], n), tok("punct", ":", n)]
                    + typ(d["type"], n)
                    + [tok("punct", "=", n)]
                    + expr(d["value"])
                )
            elif kind == "set":
                ts += [tok("id", d["name"], n), tok("punct", assignment, n)] + expr(
                    d["value"]
                )
            elif kind in ("record", "enum"):
                if d["export"]:
                    ts.insert(0, tok("keyword", "export", n))
                ts += [tok("id", d["name"], n)]
                if d.get("type_params"):
                    ts.append(tok("punct", "[", n))
                    for index, parameter in enumerate(d["type_params"]):
                        if index:
                            ts.append(tok("punct", ",", n))
                        ts.append(tok("id", parameter["name"], n))
                    ts.append(tok("punct", "]", n))
                ts.append(tok("punct", "(", n))
                for i, field in enumerate(d.get("fields", [])):
                    if i:
                        ts.append(tok("punct", ",", n))
                    ts += [tok("str", field["name"], n), tok("punct", ":", n)] + typ(
                        field["type"], n
                    )
                if kind == "enum":
                    for index, variant in enumerate(d["variants"]):
                        if index:
                            ts.append(tok("punct", ",", n))
                        ts += [
                            tok("str", variant.data["name"], variant),
                            tok("punct", "(", variant),
                        ]
                        for j, parameter in enumerate(variant.data["params"]):
                            if j:
                                ts.append(tok("punct", ",", variant))
                            ts += [
                                tok("str", parameter["name"], variant),
                                tok("punct", ":", variant),
                            ] + typ(parameter["type"], variant)
                        ts.append(tok("punct", ")", variant))
                ts.append(tok("punct", ")", n))
            elif kind == "field_set":
                ts += (
                    expr(d["target"])
                    + [
                        tok("punct", "@", n),
                        tok("str", d["name"], n),
                        tok("punct", assignment, n),
                    ]
                    + expr(d["value"])
                )
            elif kind == "index_set":
                ts += (
                    expr(d["target"])
                    + [tok("punct", "[", n)]
                    + expr(d["index"])
                    + [tok("punct", "]", n), tok("punct", assignment, n)]
                    + expr(d["value"])
                )
            elif kind in ("print", "return", "expr") and d["value"] is not None:
                ts += expr(d["value"])
            elif kind == "for":
                ts += [tok("id", d["name"], n)]
                if "value_name" in d:
                    ts += [tok("punct", ",", n), tok("id", d["value_name"], n)]
                ts += [tok("keyword", "in", n)] + expr(d["value"])
            elif kind == "match_stmt":
                ts += expr(d["value"])
            elif kind == "case":
                ts += pattern(n)
            elif kind in ("if", "while"):
                ts += expr(d["condition"])
            elif kind == "import":
                ts += [tok("id", d["module"], n)]
            elif kind in ("fn", "lambda_body"):
                if d["export"]:
                    ts.insert(0, tok("keyword", "export", n))
                ts += [tok("id", d["name"], n)]
                if d.get("type_params"):
                    ts.append(tok("punct", "[", n))
                    for index, parameter in enumerate(d["type_params"]):
                        if index:
                            ts.append(tok("punct", ",", n))
                        ts.append(tok("id", parameter["name"], n))
                    ts.append(tok("punct", "]", n))
                ts.append(tok("punct", "(", n))
                for i, p in enumerate(d["params"]):
                    if i:
                        ts.append(tok("punct", ",", n))
                    ts += [tok("id", p["name"], n), tok("punct", ":", n)] + typ(
                        p["type"], n
                    )
                ts += [tok("punct", ")", n), tok("punct", "->", n)] + typ(d["type"], n)
            rows.append((depth, ts))
            if kind == "match_stmt":
                block(d["cases"], depth + 1)
            if kind in ("if", "while", "fn", "lambda_body", "try", "for", "case"):
                block(d["body"], depth + 1)
            if kind == "try":
                rows.append(
                    (depth, [tok("keyword", "catch", n), tok("id", d["name"], n)])
                )
                block(d["otherwise"], depth + 1)
            if kind == "if" and d["otherwise"]:
                rows.append((depth, [tok("keyword", "else", n)]))
                block(d["otherwise"], depth + 1)

    block(tree.data["body"], 0)
    for index, node in enumerate(lambdas):
        block(
            [
                Node(
                    "lambda_body",
                    {**node.data, "name": index, "export": False},
                    node.span,
                )
            ],
            0,
        )
    pixels, mapping = [], {}
    for y, (depth, tokens) in enumerate(rows):
        x = depth * 2
        for kind, value, span in tokens:
            for color in encode_token(kind, value):
                pixels.append({"position": [x, y * 2], "rgba": color})
                if span and span.to_dict().get("kind") == "text":
                    mapping[f"{x},{y * 2}"] = span.to_dict()
                x += 2
    result = new_source(
        max((p["position"][0] + 1 for p in pixels), default=1),
        max(1, len(rows) * 2 - 1),
        pixels,
    )
    if annotations:
        result["metadata"].update(annotations)
    if mapping:
        result["metadata"]["source_map"] = mapping
    return result
