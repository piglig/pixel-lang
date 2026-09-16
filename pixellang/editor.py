"""Source symbol identities derived from the compiler's resolved AST.

No evaluation or image construction is needed for editor navigation. Offsets are
Unicode code points; editor hosts convert them to their own position encoding.
"""

from .model import Node, PixelError
from .project import build_project, builtin_source, import_path, walk
from .text import RESERVED, Source, TextParser, lex, parse_text
from .typeexpr import parse_type
from .typesys import BUILTINS, ERROR_FIELDS


class SymbolIndex:
    def __init__(self, files, entry="main.pxl", tolerant=False):
        self.files, self.entry = dict(files), entry
        self.implicit_aliases = {}
        self.project = build_project(files, entry, analysis=True, tolerant=tolerant)
        self.symbols = {}
        self.capture_aliases = {}
        self.occurrences = {}
        self.shorthands = {}
        self.names = {v: k for k, v in self.project["symbols"].items()}
        for mid, tree in self.project["trees"].items():
            self._declarations(tree, mid, f"{mid}:module")
        for mid, tree in self.project["trees"].items():
            self._references(tree, mid, f"{mid}:module")
            aliases = {
                value["alias"]: int(key)
                for key, value in self.project["annotations"][mid]["imports"].items()
            }
            for ref in tree.data.get("_type_refs", []):
                name = ref["name"]
                if name.startswith("$"):
                    owner = self.project["symbols"].get(ref["owner"])
                    identifier = self.project["symbols"].get(name[1:])
                    self._use(
                        ("type_parameter", mid, ref["owner_kind"], owner, identifier),
                        ref["span"],
                    )
                    continue
                target = mid
                if "." in name:
                    alias, name = name.split(".", 1)
                    target = aliases.get(alias)
                    self._use(("module", mid, target), ref["module_span"])
                identifier = self.project["symbols"].get(name)
                self._use(("record", target, identifier), ref["span"])
                self._use(("enum", target, identifier), ref["span"])

    def _use(self, key, span, declaration=False):
        while key in self.capture_aliases:
            key = self.capture_aliases[key]
        if span is not None and key in self.symbols:
            self.occurrences[(span.source, span.start, span.end, key)] = {
                "symbol": key,
                "span": span,
                "declaration": declaration,
            }

    def _declare(self, key, name, kind, span, anchor, typ=None):
        if span is None:
            return
        self.symbols[key] = {
            "name": name,
            "kind": kind,
            "span": span,
            "type": typ,
            "anchor": (span.source, anchor, kind),
        }
        self._use(key, span, True)

    def _children(self, node):
        for value in node.data.values():
            if isinstance(value, Node):
                yield value
            elif isinstance(value, list):
                yield from (item for item in value if isinstance(item, Node))

    def _declarations(self, node, mid, owner):
        d, kind = node.data, node.kind
        if d.get("_invalid"):
            return
        if kind in ("record", "enum", "fn"):
            for param in d.get("type_params", []):
                span = param.get("_name_span")
                self._declare(
                    ("type_parameter", mid, kind, d["name"], param["name"]),
                    self.names[param["name"]],
                    "type_parameter",
                    span,
                    span.start if span else 0,
                )
        if kind in ("fn", "lambda"):
            if kind == "lambda":
                for index, slot in enumerate(d["_capture_slots"]):
                    self.capture_aliases[
                        ("local", d["_function"], len(d["params"]) + index)
                    ] = ("local", owner, slot)
            owner = d["_function"] if kind == "lambda" else f"{mid}:{d['name']}"
            if kind == "fn":
                self._declare(
                    ("fn", owner),
                    self.names[d["name"]],
                    kind,
                    d.get("_name_span"),
                    node.span.start,
                    d["type"],
                )
            for param in d["params"]:
                span = param.get("_name_span")
                self._declare(
                    ("local", owner, param["_slot"]),
                    self.names[param["name"]],
                    "parameter",
                    span,
                    span.start,
                    param["type"],
                )
        elif kind == "record":
            self._declare(
                (kind, mid, d["name"]),
                self.names[d["name"]],
                kind,
                d.get("_name_span"),
                node.span.start,
            )
            typ = f"@{0 if mid == 'main' else mid}:{d['name']}"
            for field in d["fields"]:
                span = field.get("_name_span")
                self._declare(
                    ("field", typ, field["name"]),
                    field["name"],
                    "field",
                    span,
                    span.start,
                    field["type"],
                )
        elif kind == "enum":
            self._declare(
                (kind, mid, d["name"]),
                self.names[d["name"]],
                kind,
                d.get("_name_span"),
                node.span.start,
            )
            typ = f"@{0 if mid == 'main' else mid}:{d['name']}"
            for variant in d["variants"]:
                span = variant.data.get("_name_span")
                self._declare(
                    ("variant", typ, variant.data["name"]),
                    variant.data["name"],
                    "variant",
                    span,
                    span.start if span else node.span.start,
                )
        elif kind == "case":
            for param in d["params"]:
                span = param.get("_name_span")
                self._declare(
                    ("local", owner, param["_slot"]),
                    self.names[param["name"]],
                    "variable",
                    span,
                    span.start if span else node.span.start,
                    param["type"],
                )
        elif kind == "import":
            if d.get("_alias_span") is None:
                self.implicit_aliases[("module", mid, d["module"])] = node.span.end
            self._declare(
                ("module", mid, d["module"]),
                d["_alias"],
                "module",
                d.get("_alias_span") or d["_path_span"],
                node.span.start,
            )
        elif kind in ("let", "for", "try"):
            self._declare(
                ("local", owner, d["_slot"]),
                self.names[d["name"]],
                "variable",
                d.get("_name_span"),
                node.span.start,
                d.get("type"),
            )
        if kind == "for" and "value_name" in d:
            self._declare(
                ("local", owner, d["_value_slot"]),
                self.names[d["value_name"]],
                "variable",
                d.get("_value_name_span"),
                d["_value_name_span"].start
                if d.get("_value_name_span")
                else node.span.start,
            )
        for child in self._children(node):
            self._declarations(child, mid, owner)

    def _references(self, node, mid, owner):
        d, kind = node.data, node.kind
        if d.get("_invalid"):
            return
        if kind in ("fn", "lambda"):
            owner = d["_function"] if kind == "lambda" else f"{mid}:{d['name']}"
        if kind in ("variable", "set"):
            key = (
                ("fn", d["_function"])
                if "_function" in d
                else ("local", owner, d["_slot"])
            )
            self._use(key, d.get("_name_span"))
        elif kind == "call" and "_callable_type" in d:
            self._use(("local", owner, d["_slot"]), d.get("_name_span"))
        elif kind in ("call", "function_value"):
            self._use(
                ("fn", d.get("_declaration", d["_function"])), d.get("_name_span")
            )
            self._use(("module", mid, d["module"]), d.get("_module_span"))
        elif kind in ("field", "field_set"):
            self._use(
                ("field", parse_type(d["target"].data["_type"]).name, d["name"]),
                d.get("_name_span"),
            )
        elif kind in ("enum_value", "case") and d.get("variant") is not None:
            self._use(
                ("variant", parse_type(d["enum_type"]).name, d["variant"]),
                d.get("_variant_span"),
            )
        elif kind == "record_value":
            for name, span, value in zip(
                d["names"], d.get("_field_spans", []), d["items"]
            ):
                field_key = ("field", parse_type(d["record_type"]).name, name)
                self._use(field_key, span)
                variable_span = value.data.get("_name_span")
                if value.kind == "variable" and variable_span == span:
                    self.shorthands[(span.source, span.start, span.end)] = (
                        field_key,
                        ("local", owner, value.data["_slot"]),
                    )
        for child in self._children(node):
            self._references(child, mid, owner)

    def at(self, source, offset):
        hits = [
            item
            for (filename, start, end, _), item in self.occurrences.items()
            if filename == source and start <= offset < end
        ]
        return (
            min(
                hits,
                key=lambda item: (
                    item["span"].end - item["span"].start,
                    item["symbol"][0] != "local",
                ),
            )
            if hits
            else None
        )

    def definition(self, source, offset):
        hit = self.at(source, offset)
        return self.symbols[hit["symbol"]]["span"].to_dict() if hit else None

    def references(self, source, offset, include_declaration=True):
        hit = self.at(source, offset)
        if not hit:
            return []
        return [
            item["span"].to_dict()
            for _, item in sorted(self.occurrences.items())
            if item["symbol"] == hit["symbol"]
            and (include_declaration or not item["declaration"])
        ]

    def rename(self, source, offset, new_name):
        if (
            not isinstance(new_name, str)
            or not new_name.isidentifier()
            or len(new_name) > 128
            or new_name in RESERVED
        ):
            raise PixelError("editor", "Rename requires a non-reserved identifier")
        hit = self.at(source, offset)
        if hit is None:
            raise PixelError("editor", "No symbol at this position")
        key = hit["symbol"]
        edits = []
        for occurrence in self.occurrences.values():
            if occurrence["symbol"] != key:
                continue
            span = occurrence["span"]
            if span.source not in self.files:
                raise PixelError(
                    "editor", "Cannot rename a bundled library symbol", span
                )
            if occurrence["declaration"] and key in self.implicit_aliases:
                end = self.implicit_aliases[key]
                edits.append(
                    (
                        Source(self.files[span.source], span.source).span(end, end),
                        " as " + new_name,
                    )
                )
            else:
                pair = self.shorthands.get((span.source, span.start, span.end))
                if pair:
                    field_key, variable_key = pair
                    field_name = (
                        new_name
                        if key == field_key
                        else self.symbols[field_key]["name"]
                    )
                    variable_name = (
                        new_name
                        if key == variable_key
                        else self.symbols[variable_key]["name"]
                    )
                    edits.append((span, field_name + ": " + variable_name))
                else:
                    edits.append((span, new_name))
        edits.sort(key=lambda edit: (edit[0].source, edit[0].start))
        changed = dict(self.files)
        for span, replacement in reversed(edits):
            text = changed[span.source]
            changed[span.source] = text[: span.start] + replacement + text[span.end :]
        after = SymbolIndex(changed, self.entry)

        def translated(filename, position):
            return position + sum(
                len(text) - (span.end - span.start)
                for span, text in edits
                if span.source == filename
                and span.end <= position
                and span.start < position
            )

        # Compilation alone cannot detect capture by a same-typed shadow binding.
        # Compare every reference's declaration anchor, including untouched names.
        for occurrence in self.occurrences.values():
            original_key = occurrence["symbol"]
            if occurrence["declaration"] and original_key in self.implicit_aliases:
                continue
            span = occurrence["span"]
            position = translated(span.source, span.start)
            pair = self.shorthands.get((span.source, span.start, span.end))
            if pair and key in pair and original_key == pair[1]:
                field_name = (
                    new_name if key == pair[0] else self.symbols[pair[0]]["name"]
                )
                position += len(field_name) + 2
            if pair and key not in pair:
                # Both identities still share one token: resolve by symbol kind.
                resolved = next(
                    (
                        item
                        for (
                            filename,
                            start,
                            end,
                            identity,
                        ), item in after.occurrences.items()
                        if filename == span.source
                        and start == position
                        and identity[0] == original_key[0]
                    ),
                    None,
                )
            else:
                resolved = after.at(span.source, position)
            filename, anchor, kind = self.symbols[original_key]["anchor"]
            expected = (filename, translated(filename, anchor), kind)
            if (
                resolved is None
                or after.symbols[resolved["symbol"]]["anchor"] != expected
            ):
                raise PixelError(
                    "editor", "Rename would change a reference's binding", span
                )
        return [{"span": span.to_dict(), "text": text} for span, text in edits]


def complete(files, entry, source, offset):
    """Analyze a temporary cursor expression; never execute incomplete programs."""
    text = files[source]
    if not 0 <= offset <= len(text):
        raise PixelError("editor", "Completion offset is outside source")
    start = offset
    while start and (text[start - 1].isalnum() or text[start - 1] == "_"):
        start -= 1
    end = offset
    while end < len(text) and (text[end].isalnum() or text[end] == "_"):
        end += 1
    prefix = text[start:offset]
    sentinel = "__pixel_editor_cursor__"
    patched = text[:start] + sentinel + text[end:]
    # Complete unmatched delimiters at EOF for a function currently being typed.
    try:
        tokens = TextParser(patched, source, tolerant=True).tokens
    except PixelError:
        return []
    cursor = next((t for t in tokens if t.start == start and t.value == sentinel), None)
    if cursor is None:  # Cursor is in a comment or a string.
        return []
    stack = []
    pairs = {"(": ")", "[": "]", "{": "}"}
    for token in tokens:
        if token.kind == "str":
            continue
        if token.value in pairs:
            stack.append(pairs[token.value])
        elif token.value in (")", "]", "}") and stack and stack[-1] == token.value:
            stack.pop()
    patched += "\n" + "".join(reversed(stack))
    # A type reference can be incomplete even when the declaration syntax parses.
    # Resolve its namespace from syntax before asking the checker for value types.
    try:
        syntax = parse_text(patched, source, tolerant=True)
        type_ref = next(
            (
                ref
                for ref in syntax.data.get("_type_refs", [])
                + syntax.data.get("_incomplete_type_refs", [])
                if ref["span"].start == start
            ),
            None,
        )
        if type_ref:
            target, external = syntax, False
            if "." in type_ref["name"]:
                alias = type_ref["name"].split(".", 1)[0]
                imported = next(
                    (
                        item
                        for item in syntax.data["body"]
                        if item.kind == "import" and item.data["_alias"] == alias
                    ),
                    None,
                )
                if imported is None:
                    return []
                filename = import_path(source, imported.data["module"])
                content = files.get(filename)
                if content is None:
                    content = builtin_source(filename)
                if content is None:
                    return []
                target, external = parse_text(content, filename), True
            candidates = [
                {
                    "label": item.data["name"],
                    "kind": "Enum" if item.kind == "enum" else "Struct",
                    "detail": item.kind + " " + item.data["name"],
                }
                for item in target.data["body"]
                if item.kind in ("record", "enum")
                and (not external or item.data["export"])
            ]
            if not external:
                candidates += [
                    {
                        "label": parameter["name"],
                        "kind": "TypeParameter",
                        "detail": "type parameter",
                    }
                    for declaration in syntax.data["body"]
                    if declaration.span.start <= start <= declaration.span.end
                    for parameter in declaration.data.get("type_params", [])
                ]
                candidates += [
                    {"label": name, "kind": "Keyword", "detail": "type"}
                    for name in (
                        "int",
                        "float64",
                        "bool",
                        "string",
                        "json",
                        "map",
                        "Error",
                    )
                ]
                candidates += [
                    {"label": item.data["_alias"], "kind": "Module", "detail": "module"}
                    for item in syntax.data["body"]
                    if item.kind == "import"
                ]
            return sorted(
                (item for item in candidates if item["label"].startswith(prefix)),
                key=lambda item: item["label"],
            )
    except PixelError:
        return []
    changed = {**files, source: patched}
    try:
        project = build_project(changed, entry, analysis=True, tolerant=True)
    except PixelError:
        return []
    mid = project["modules"][source]
    tree = project["trees"][mid]
    names = {value: name for name, value in project["symbols"].items()}
    cursor_nodes = [
        node
        for node in walk(tree)
        if any(
            node.data.get(key) is not None and node.data[key].start == start
            for key in ("_name_span", "_variant_span")
        )
    ]
    if not cursor_nodes:
        return []
    node = cursor_nodes[0]

    def display_type(typ):
        if not typ:
            return ""

        def display_name(identity):
            if identity.startswith("@") and ":" in identity:
                return names.get(int(identity.split(":")[1]), identity)
            if identity.isdecimal():
                return names.get(int(identity), identity)
            return identity

        try:
            return parse_type(typ).display(display_name)
        except ValueError:
            return typ

    def declaration(item):
        d = item.data
        name = names[d["name"]]
        if item.kind in ("record", "enum"):
            return {
                "label": name,
                "kind": "Enum" if item.kind == "enum" else "Struct",
                "detail": item.kind + " " + name,
            }
        signature = ", ".join(
            names[p["name"]] + ": " + display_type(p["type"]) for p in d["params"]
        )
        return {
            "label": name,
            "kind": "Function",
            "detail": "fn "
            + name
            + (
                "[" + ", ".join(names[p["name"]] for p in d["type_params"]) + "]"
                if d.get("type_params")
                else ""
            )
            + "("
            + signature
            + ") -> "
            + display_type(d["type"]),
        }

    candidates = []
    if node.kind in ("enum_value", "case"):
        schema = (
            project["trees"]["main"]
            .data.get("_enum_instances", {})
            .get(node.data.get("enum_type"), {})
        )
        candidates = [
            {
                "label": tag,
                "kind": "EnumMember",
                "detail": tag
                + (
                    "("
                    + ", ".join(
                        name + ": " + display_type(typ) for name, typ in fields.items()
                    )
                    + ")"
                    if fields
                    else ""
                ),
            }
            for tag, fields in schema.items()
        ]
    elif node.kind in ("field", "call", "function_value") and text[
        :start
    ].rstrip().endswith("."):
        target = node.data.get("target")
        alias = (
            names.get(target.data.get("name"))
            if target and target.kind == "variable" and "_type" not in target.data
            else None
        )
        if node.kind in ("call", "function_value"):
            imported = node.data.get("module")
        else:
            imported = next(
                (
                    int(key)
                    for key, info in project["annotations"][mid]["imports"].items()
                    if info["alias"] == alias
                ),
                None,
            )
        if imported is not None:
            candidates = [
                declaration(item)
                for item in project["trees"][imported].data["body"]
                if item.kind in ("fn", "record", "enum") and item.data["export"]
            ]
        else:
            typ = target.data.get("_type") if target else None
            schema = (
                project["trees"]["main"].data.get("_record_instances", {}).get(typ, {})
            )
            candidates = [
                {
                    "label": field_name,
                    "kind": "Field",
                    "detail": display_type(field_type),
                }
                for field_name, field_type in schema.items()
            ]
        if target and target.data.get("_type") == "Error":
            candidates = [
                {"label": field, "kind": "Field", "detail": "string"}
                for field in ERROR_FIELDS
            ]
    else:
        candidates = [
            declaration(item)
            for item in tree.data["body"]
            if item.kind in ("fn", "record", "enum")
        ]
        candidates += [
            {"label": info["alias"], "kind": "Module", "detail": info["file"]}
            for info in project["annotations"][mid]["imports"].values()
        ]
        candidates += [
            {"label": names[name], "kind": "Variable", "detail": display_type(typ)}
            for name, (typ, slot, mutable) in node.data.get("_visible", {}).items()
        ]
        candidates += [
            {"label": name, "kind": "Function", "detail": "PixelLang builtin"}
            for name in BUILTINS
        ]
        candidates += [
            {"label": name, "kind": "Keyword", "detail": "PixelLang"}
            for name in RESERVED - set(BUILTINS)
        ]
    return sorted(
        (item for item in candidates if item["label"].startswith(prefix)),
        key=lambda item: item["label"],
    )


def format_source(text, filename):
    """Normalize indentation without regenerating tokens, comments or literals."""
    source = Source(text, filename)
    tokens = lex(source)
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]] + [parts[-1]]
    by_line, protected = {}, set()
    from bisect import bisect_right

    for token in tokens:
        first = bisect_right(source.lines, token.start) - 1
        last = bisect_right(source.lines, max(token.start, token.end - 1)) - 1
        if first != last:
            protected.update(range(first, last + 1))
        if token.kind != "separator":
            by_line.setdefault(first, []).append(token)
    depth, output = 0, []
    for index, line in enumerate(lines):
        line_tokens = by_line.get(index, [])
        closing = 0
        for token in line_tokens:
            if token.kind == "str" or token.value not in ("}", "]", ")"):
                break
            closing += 1
        stripped = line.lstrip(" \t")
        output.append(
            line
            if index in protected or not stripped.strip()
            else "    " * max(0, depth - closing) + stripped
        )
        for token in line_tokens:
            if token.kind == "str":
                continue
            if token.value in ("{", "[", "("):
                depth += 1
            elif token.value in ("}", "]", ")"):
                depth = max(0, depth - 1)
    return "".join(output)
