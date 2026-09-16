"""Multi-file human source → typed spatial project → shared compiler."""

import copy
import posixpath
import re
from pathlib import Path

from .canonical import encode
from .compiler import compile_source, frontend
from .model import Node, PixelError
from .parser import PRECEDENCE
from .semantics import Checker
from .text import RESERVED, parse_text
from .typeexpr import Type, parse_type


def safe_name(name):
    if (
        not isinstance(name, str)
        or not name
        or "\\" in name
        or "\x00" in name
        or name.startswith("/")
    ):
        raise PixelError("project", "Project paths must be relative POSIX paths")
    normalized = posixpath.normpath(name)
    if (
        normalized == ".."
        or normalized.startswith("../")
        or not normalized.endswith(".pxl")
    ):
        raise PixelError(
            "project", "Module path must stay inside the project and end in .pxl"
        )
    return normalized


def walk(node):
    if isinstance(node, Node):
        yield node
        for value in node.data.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def builtin_source(name):
    if not name.startswith("std/"):
        return None
    target = Path(__file__).parent / "stdlib" / name[4:]
    if not target.is_file():
        return None
    return target.read_text(encoding="utf-8")


def import_path(filename, module):
    return safe_name(
        module
        if module.startswith("std/")
        else posixpath.join(posixpath.dirname(filename), module)
    )


def build_project(files, entry="main.pxl", *, analysis=False, tolerant=False):
    if tolerant and not analysis:
        raise PixelError("project", "Recovered syntax is available for analysis only")
    if not isinstance(files, dict) or len(files) > 128:
        raise PixelError("project", "Expected a project with at most 128 files")
    sources = {safe_name(k): v for k, v in files.items()}
    if len(sources) != len(files):
        raise PixelError("project", "Duplicate normalized project paths")
    entry = safe_name(entry)
    trees = {}
    visiting = set()

    def visit(name, import_span=None):
        if name in visiting:
            raise PixelError("semantic", f"Cyclic import: {name}", import_span)
        if name in trees:
            return
        if name not in sources:
            library = builtin_source(name)
            if library is not None:
                if len(sources) >= 128:
                    raise PixelError(
                        "project", "Project and standard libraries exceed 128 files"
                    )
                sources[name] = library
        if name not in sources:
            raise PixelError("project", f"Module not found: {name}", import_span)
        visiting.add(name)
        tree = parse_text(sources[name], name, tolerant=tolerant)
        for node in tree.data["body"]:
            if node.kind == "import":
                target = import_path(name, node.data["module"])
                node.data["_path"] = target
                visit(target, node.span)
        visiting.remove(name)
        trees[name] = tree

    visit(entry)
    if analysis:
        for filename in sorted(files):
            visit(safe_name(filename))
    entry_tree = trees[entry]
    main = next(
        (
            n
            for n in entry_tree.data["body"]
            if n.kind == "fn" and n.data["name"] == "main"
        ),
        None,
    )
    for tree in trees.values():
        for statement in tree.data["body"]:
            if statement.kind not in ("fn", "record", "enum", "import"):
                raise PixelError(
                    "semantic",
                    "Modules may contain declarations only; move execution into fn main()",
                    statement.span,
                )
    if main is None and not analysis:
        raise PixelError("semantic", "Entry module requires fn main()", entry_tree.span)
    if main is not None:
        if (
            main.data["params"]
            or main.data.get("type_params")
            or main.data["type"] != "unit"
        ):
            raise PixelError(
                "semantic",
                "Entry main must have no parameters or return value",
                main.span,
            )
        main.data["entry"] = True
    names = set()
    for tree in trees.values():
        for node in walk(tree):
            if node.kind in (
                "let",
                "set",
                "variable",
                "fn",
                "call",
                "function_value",
                "try",
                "for",
                "record",
                "enum",
            ):
                names.add(node.data["name"])
            if node.kind == "for" and "value_name" in node.data:
                names.add(node.data["value_name"])
            names.update(p["name"] for p in node.data.get("type_params", []))
            if node.kind in ("fn", "case", "lambda"):
                names.update(p["name"] for p in node.data["params"])
    if len(names) > 65536:
        raise PixelError("project", "Identifier space exhausted")
    symbols = {name: i for i, name in enumerate(sorted(names))}
    modules = {
        name: i + 1 for i, name in enumerate(sorted(n for n in trees if n != entry))
    }
    modules[entry] = "main"
    annotations = {}
    typed = {}
    for filename, tree in trees.items():
        aliases = {}
        imports = {}
        for node in tree.data["body"]:
            if node.kind == "import":
                alias = node.data["_alias"]
                target = node.data["_path"]
                if alias in aliases:
                    raise PixelError(
                        "semantic", f"Duplicate module alias {alias}", node.span
                    )
                aliases[alias] = modules[target]
                imports[str(modules[target])] = {
                    "alias": alias,
                    "path": node.data["module"],
                    "file": target,
                }

        def resolve_type(value, span, *, tree=tree, filename=filename, aliases=aliases):
            if value is None or value == "unit":
                return value

            def resolve(item):
                args = tuple(resolve(arg) for arg in item.args)
                if item.kind == "parameter":
                    return Type("parameter", str(symbols[item.name]))
                if item.kind != "nominal" or not item.name.startswith("@"):
                    return Type(item.kind, item.name, args)
                spelling = item.name[1:]
                target = modules[filename]
                if "." in spelling:
                    alias, spelling = spelling.split(".", 1)
                    if alias not in aliases:
                        raise PixelError("type", f"Unknown type module {alias}", span)
                    target = aliases[alias]
                if spelling not in symbols:
                    if tolerant:
                        return Type("nominal", "@invalid", args)
                    raise PixelError("type", f"Unknown record {spelling}", span)
                return Type(
                    "nominal",
                    f"@{0 if target == 'main' else target}:{symbols[spelling]}",
                    args,
                )

            return resolve(parse_type(value)).spelling()

        for node in walk(tree):
            if node.kind == "import" and node not in tree.data["body"]:
                raise PixelError("semantic", "Imports must be module-level", node.span)
            d = node.data
            if node.kind in ("field", "field_set", "record", "record_value"):
                tree.data["_language"] = "0.8"
            for key in ("type", "_declared_type", "record_type", "enum_type"):
                if key in d:
                    d[key] = resolve_type(d[key], node.span)
            if "type_args" in d:
                d["type_args"] = [resolve_type(t, node.span) for t in d["type_args"]]
            for param in d.get("type_params", []):
                param["name"] = symbols[param["name"]]
            for field in d.get("fields", []) + d.get("params", []):
                if "type" in field:
                    field["type"] = resolve_type(field["type"], node.span)
            if node.kind in ("case", "lambda"):
                for param in d["params"]:
                    param["name"] = symbols[param["name"]]
            if node.kind == "import":
                d["module"] = modules[d["_path"]]
            elif node.kind in (
                "let",
                "set",
                "variable",
                "fn",
                "call",
                "function_value",
                "try",
                "for",
                "record",
                "enum",
            ):
                if node.kind in ("call", "function_value"):
                    d["_source_member_name"] = d["name"]
                d["name"] = symbols[d["name"]]
                if node.kind == "for" and "value_name" in d:
                    d["value_name"] = symbols[d["value_name"]]
                if node.kind == "fn":
                    for param in d["params"]:
                        param["name"] = symbols[param["name"]]
                if node.kind in ("call", "function_value") and d["module"] is not None:
                    if d["module"] not in aliases:
                        raise PixelError(
                            "semantic",
                            f"Module {d['module']} is not imported",
                            node.span,
                        )
                    d["_module_binding"] = symbols.get(d["module"])
                    d["module"] = aliases[d["module"]]
        mid = modules[filename]
        typed[mid] = tree
        annotations[mid] = {
            "filename": filename,
            "symbols": {str(v): k for k, v in symbols.items()},
            "imports": imports,
        }
    try:
        type_names = {str(value): name for name, value in symbols.items()}
        for mid, tree in typed.items():
            prefix = annotations[mid]["filename"].removesuffix(".pxl").replace("/", ".")
            for node in tree.data["body"]:
                if node.kind in ("record", "enum"):
                    name = type_names[str(node.data["name"])]
                    type_names[
                        f"@{0 if mid == 'main' else mid}:{node.data['name']}"
                    ] = name if mid == "main" else f"{prefix}.{name}"
        checker = Checker(
            typed, tolerant=tolerant, require_entry=not analysis, type_names=type_names
        )
        checker.check()
    except PixelError as error:
        reverse = {str(v): k for k, v in symbols.items()}
        error.message = re.sub(
            r"#([0-9]+)", lambda m: reverse.get(m[1], m[0]), error.message
        )
        raise
    if analysis:
        return {
            "trees": typed,
            "annotations": annotations,
            "sources": sources,
            "symbols": symbols,
            "modules": modules,
            "errors": [
                error
                for tree in typed.values()
                for error in tree.data.get("_syntax_errors", [])
            ]
            + checker.errors,
        }
    documents = {mid: encode(tree, annotations[mid]) for mid, tree in typed.items()}
    main_doc = documents.pop("main")
    main_doc["bundle"] = {
        "version": "0.8",
        "entry": entry,
        "modules": {str(k): v for k, v in documents.items()},
    }
    return main_doc


def compile_project(files, entry="main.pxl", optimized=True):
    doc = build_project(files, entry)
    result = compile_source(doc, entry, optimized=optimized)
    return doc, result


def read_project(path):
    path = Path(path).resolve()
    root = path.parent
    files = {}

    def read(name):
        name = safe_name(name)
        if name in files:
            return
        if len(files) >= 128:
            raise PixelError("project", "Project exceeds 128 files")
        target = (root / name).resolve()
        if not target.is_relative_to(root):
            raise PixelError("project", "Import escapes the project root")
        try:
            text = builtin_source(name) if not target.exists() else None
            if text is None:
                if target.stat().st_size > 4_000_000:
                    raise PixelError("project", "Text file exceeds 4MB")
                text = target.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as e:
            raise PixelError("project", f"Cannot read {name}: {e}") from e
        files[name] = text
        tree = parse_text(text, name)
        for node in tree.data["body"]:
            if node.kind == "import":
                try:
                    read(import_path(name, node.data["module"]))
                except PixelError as e:
                    if not e.span:
                        e.span = node.span
                    raise

    read(path.name)
    return files, path.name


def text_diagnostic(error, doc):
    data = error.to_dict() if isinstance(error, PixelError) else copy.deepcopy(error)
    span = data.get("span")
    if not span or span.get("kind") == "text":
        return data
    candidates = [doc] + list(doc.get("bundle", {}).get("modules", {}).values())
    source = next(
        (
            d
            for d in candidates
            if d.get("metadata", {}).get("filename") == span["source"]
        ),
        doc,
    )
    mapping = source.get("metadata", {}).get("source_map", {})
    found = [mapping.get(",".join(map(str, p))) for p in span.get("points", [])]
    found = [s for s in found if isinstance(s, dict) and s.get("kind") == "text"]
    if found:
        # Prefer the tightest mapped expression; keep spatial provenance alongside it.
        text = min(found, key=lambda s: s["end"]["offset"] - s["start"]["offset"])
        data["spatial_span"] = span
        data["span"] = text
    return data


def project_text(doc):
    """Recover readable canonical source from decoded image semantics, never stored text."""
    docs = [doc] + list(doc.get("bundle", {}).get("modules", {}).values())
    files = {}
    for i, item in enumerate(docs):
        meta = item.get("metadata", {})
        filename = safe_name(meta.get("filename", f"module{i}.pxl"))
        tree, _ = frontend(item, filename)
        module = (
            "0"
            if i == 0
            else next(
                key
                for key, value in doc.get("bundle", {}).get("modules", {}).items()
                if value is item
            )
        )
        files[filename] = format_tree(tree, {**meta, "record_module": module})
    return {
        "entry": doc.get("bundle", {}).get("entry", next(iter(files))),
        "files": files,
    }


def format_tree(tree, metadata=None):
    metadata = metadata or {}
    symbols = metadata.get("symbols", {})
    if not isinstance(symbols, dict):
        symbols = {}
    imports = metadata.get("imports", {})
    if not isinstance(imports, dict):
        imports = {}

    entry_name = next(
        (
            node.data["name"]
            for node in tree.data["body"]
            if node.kind == "fn" and node.data.get("entry")
        ),
        None,
    )

    def name(value):
        if value == entry_name:
            return "main"
        candidate = symbols.get(str(value), f"v{value}")
        return (
            candidate
            if isinstance(candidate, str)
            and candidate.isidentifier()
            and candidate not in RESERVED
            else f"v{value}"
        )

    def typ(value):
        def display_name(identity):
            if identity.isdecimal():
                return name(int(identity))
            if not identity.startswith("@"):
                return identity
            module, identifier = identity[1:].split(":")
            current = str(metadata.get("record_module", "0"))
            prefix = (
                ""
                if module == current
                else imports.get(module, {}).get("alias", f"module{module}") + "."
            )
            return prefix + name(int(identifier))

        return parse_type(value).display(display_name)

    def pattern(case):
        d = case.data
        if d["variant"] is None:
            return "_"
        return (
            typ(d["enum_type"])
            + "."
            + d["variant"]
            + (
                "(" + ", ".join(name(p["name"]) for p in d["params"]) + ")"
                if d["params"]
                else ""
            )
        )

    def expr(node, expected=None, minimum=0):
        d = node.data
        k = node.kind
        if k == "enum_value":
            return (
                typ(d["enum_type"])
                + "."
                + d["variant"]
                + (
                    "(" + ", ".join(expr(arg) for arg in d["args"]) + ")"
                    if d["args"]
                    else ""
                )
            )
        if k == "match":
            return (
                "(match "
                + expr(d["value"])
                + " { "
                + ", ".join(
                    pattern(case) + " => " + expr(case.data["value"])
                    for case in d["cases"]
                )
                + " })"
            )
        if k == "literal":
            if d["type"] == "str":
                return __import__("json").dumps(d["value"], ensure_ascii=False)
            if d["type"] == "bool":
                return str(d["value"]).lower()
            return str(d["value"])
        if k == "variable":
            return name(d["name"])
        if k == "unary":
            operand = expr(d["operand"], minimum=7)
            if d["op"] == "-" and operand.startswith("-"):
                operand = "(" + operand + ")"
            result = ("!" if d["op"] == "not" else "-") + operand
            return "(" + result + ")" if minimum > 7 else result
        if k == "binary":
            precedence = PRECEDENCE[d["op"]]
            result = (
                expr(d["left"], minimum=precedence)
                + " "
                + {"and": "&&", "or": "||"}.get(d["op"], d["op"])
                + " "
                + expr(d["right"], minimum=precedence + 1)
            )
            return "(" + result + ")" if precedence < minimum else result
        if k == "record_value":
            return (
                typ(d["record_type"])
                + "{"
                + ", ".join(
                    field + ": " + expr(value)
                    for field, value in zip(d["names"], d["items"])
                )
                + "}"
            )
        if k == "field":
            return expr(d["target"], minimum=8) + "." + d["name"]
        if k == "index":
            return expr(d["target"], minimum=8) + "[" + expr(d["index"]) + "]"
        if k == "map":
            declared = d.get("_declared_type") or d["_type"]
            return (
                typ(declared)
                + "{"
                + ", ".join(
                    expr(d["items"][i]) + ": " + expr(d["items"][i + 1], declared[:-2])
                    for i in range(0, len(d["items"]), 2)
                )
                + "}"
            )
        if k == "array":
            declared = d.get("_declared_type")
            values = ", ".join(
                expr(v, declared[:-2] if declared else None) for v in d["items"]
            )
            result = "[" + values + "]"
            if declared and not d["items"] and expected != declared:
                result = "(" + result + ": " + typ(declared) + ")"
            return result
        if k == "json_decode":
            return "jsonDecode[" + typ(d["type"]) + "](" + expr(d["value"]) + ")"
        if k == "lambda":
            start = len(lines)
            block(d["body"], 1)
            body = "\n".join(lines[start:])
            del lines[start:]
            parameters = ", ".join(
                name(p["name"]) + ": " + typ(p["type"]) for p in d["params"]
            )
            return (
                "fn("
                + parameters
                + ")"
                + (" -> " + typ(d["type"]) if d["type"] != "unit" else "")
                + " {\n"
                + body
                + "\n}"
            )
        if k == "invoke":
            return (
                "("
                + expr(d["target"])
                + ")("
                + ", ".join(expr(a) for a in d["args"])
                + ")"
            )
        if k in ("call", "builtin", "function_value"):
            prefix = ""
            if d.get("module") is not None:
                alias = imports.get(str(d["module"]), {}).get(
                    "alias", f"module{d['module']}"
                )
                if (
                    not isinstance(alias, str)
                    or not alias.isidentifier()
                    or alias in RESERVED
                    and alias != "json"
                ):
                    alias = f"module{d['module']}"
                prefix = alias + "."
            return (
                prefix
                + (d["name"] if k == "builtin" else name(d["name"]))
                + (
                    "[" + ", ".join(typ(t) for t in d["type_args"]) + "]"
                    if d.get("type_args")
                    else ""
                )
                + (
                    ""
                    if k == "function_value"
                    else "(" + ", ".join(expr(v) for v in d["args"]) + ")"
                )
            )
        raise PixelError("text", f"Cannot recover {k}")

    lines = []

    def block(nodes, depth):
        for n in nodes:
            d = n.data
            k = n.kind
            assignment = d["update"] + "=" if "update" in d else "="
            indent = "    " * depth
            if k == "match_stmt":
                lines.append(
                    indent
                    + ("match " + expr(d["value"]) + " {").replace("\n", "\n" + indent)
                )
                for case in d["cases"]:
                    lines.append(indent + "    " + pattern(case) + " => {")
                    block(case.data["body"], depth + 2)
                    lines.append(indent + "    }")
                lines.append(indent + "}")
                continue
            if k == "let":
                annotation = (
                    ": " + typ(d["type"])
                    if any(
                        n.kind == "array" and not n.data["items"]
                        for n in walk(d["value"])
                    )
                    else ""
                )
                line = f"{'var' if d.get('mutable', False) else 'let'} {name(d['name'])}{annotation} = {expr(d['value'], d['type'])}"
            elif k == "set":
                line = f"{name(d['name'])} {assignment} {expr(d['value'])}"
            elif k == "enum":
                type_args = (
                    "[" + ", ".join(name(p["name"]) for p in d["type_params"]) + "]"
                    if d["type_params"]
                    else ""
                )
                variants = [
                    v.data["name"]
                    + (
                        "("
                        + ", ".join(
                            p["name"] + ": " + typ(p["type"]) for p in v.data["params"]
                        )
                        + ")"
                        if v.data["params"]
                        else ""
                    )
                    for v in d["variants"]
                ]
                line = (
                    ("export " if d["export"] else "")
                    + "enum "
                    + name(d["name"])
                    + type_args
                    + " { "
                    + ", ".join(variants)
                    + " }"
                )
            elif k == "record":
                line = (
                    ("export " if d["export"] else "")
                    + "record "
                    + name(d["name"])
                    + (
                        "[" + ", ".join(name(p["name"]) for p in d["type_params"]) + "]"
                        if d.get("type_params")
                        else ""
                    )
                    + " { "
                    + ", ".join(
                        field["name"] + ": " + typ(field["type"])
                        for field in d["fields"]
                    )
                    + " }"
                )
            elif k == "field_set":
                line = (
                    expr(d["target"], minimum=8)
                    + "."
                    + d["name"]
                    + " "
                    + assignment
                    + " "
                    + expr(d["value"])
                )
            elif k == "index_set":
                line = f"{expr(d['target'])}[{expr(d['index'])}] {assignment} {expr(d['value'])}"
            elif k == "print":
                line = "print(" + expr(d["value"]) + ")"
            elif k == "expr":
                line = expr(d["value"])
            elif k in ("break", "continue"):
                line = k
            elif k == "return":
                line = "return" + (
                    " " + expr(d["value"]) if d["value"] is not None else ""
                )
            elif k == "import":
                info = imports.get(str(d["module"]), {})
                alias = info.get("alias", f"module{d['module']}")
                if (
                    not isinstance(alias, str)
                    or not alias.isidentifier()
                    or alias in RESERVED
                    and alias != "json"
                ):
                    alias = f"module{d['module']}"
                path = info.get("path", f"{d['module']}.pxl")
                safe_name(
                    posixpath.join(
                        posixpath.dirname(metadata.get("filename", "main.pxl")), path
                    )
                )
                line = "import " + __import__("json").dumps(path)
                if alias != Path(path).stem:
                    line += " as " + alias
            elif k == "for":
                bindings = name(d["name"])
                if "value_name" in d:
                    bindings += ", " + name(d["value_name"])
                line = "for " + bindings + " in (" + expr(d["value"]) + ") {"
            elif k == "try":
                line = "try {"
            elif k in ("if", "while"):
                line = (
                    ("if (" if k == "if" else "while (") + expr(d["condition"]) + ") {"
                )
            elif k == "fn":
                line = (
                    ("export " if d["export"] else "")
                    + "fn "
                    + ("main" if d.get("entry") else name(d["name"]))
                    + (
                        "[" + ", ".join(name(p["name"]) for p in d["type_params"]) + "]"
                        if d.get("type_params")
                        else ""
                    )
                    + "("
                    + ", ".join(
                        name(p["name"]) + ": " + typ(p["type"]) for p in d["params"]
                    )
                    + ")"
                    + (" -> " + typ(d["type"]) if d["type"] != "unit" else "")
                    + " {"
                )
            else:
                raise PixelError("text", f"Cannot recover statement {k}")
            if (
                k == "fn"
                and len(d["body"]) == 1
                and d["body"][0].kind == "return"
                and d["body"][0].data["value"] is not None
            ):
                lines.append(
                    indent
                    + (line[:-2] + " = " + expr(d["body"][0].data["value"])).replace(
                        "\n", "\n" + indent
                    )
                )
                if depth == 0:
                    lines.append("")
                continue
            lines.append(indent + line.replace("\n", "\n" + indent))
            if k in ("fn", "if", "while", "try", "for"):
                block(d["body"], depth + 1)
                lines.append(indent + "}")
                if k == "try":
                    lines[-1] += " catch " + name(d["name"]) + " {"
                    block(d["otherwise"], depth + 1)
                    lines.append(indent + "}")
                if k == "if" and d["otherwise"]:
                    lines[-1] += " else {"
                    block(d["otherwise"], depth + 1)
                    lines.append(indent + "}")
            if depth == 0 and k in ("fn", "import"):
                lines.append("")

    block(tree.data["body"], 0)
    return "\n".join(lines).rstrip() + "\n"
