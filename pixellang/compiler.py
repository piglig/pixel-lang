"""Bootstrap orchestration; the language core never sees RGBA bytes."""

from dataclasses import dataclass
from pathlib import Path

from . import canonical, codec, parser, spatial
from .backend import assemble, lower, optimize
from .model import Node, PixelError, valid_text_span
from .semantics import Checker


@dataclass
class Compilation:
    spatial_ast: dict
    canonical_ast: Node
    modules: dict
    ir: dict
    bytecode: dict
    source: dict | None = None

    def inspect(self):
        return {
            "spatial_ast": self.spatial_ast,
            "canonical_ast": self.canonical_ast.to_dict(),
            "modules": {str(k): v.to_dict() for k, v in self.modules.items()},
            "ir": self.ir,
            "bytecode": self.bytecode,
        }


def frontend(doc, source="<source>"):
    tokens = codec.validate(doc, source)
    regions = spatial.analyze(tokens, doc.get("spatial", {}).get("links", []))
    try:
        tree = parser.parse(regions)
    except RecursionError as e:
        raise PixelError(
            "syntax",
            "Expression nesting exceeds compiler limit",
            regions[0].span if regions else None,
        ) from e
    tree.data["_language"] = doc["versions"]["language"]
    return tree, {
        **spatial.describe(regions, doc.get("spatial", {}).get("links", [])),
        "semantic_structure": tree.to_dict(),
    }


def compile_source(doc, source="<source>", resolver=None, optimized=True):
    modules, spaces, visiting, documents = {}, {}, set(), {}
    external_resolver = resolver
    bundled = (
        doc.get("bundle", {}).get("modules", {})
        if isinstance(doc, dict) and isinstance(doc.get("bundle", {}), dict)
        else {}
    )
    if bundled:

        def resolver(mid):
            if str(mid) in bundled:
                item = bundled[str(mid)]
                return item, item.get("metadata", {}).get("filename", f"{mid}.pixel")
            if external_resolver:
                return external_resolver(mid)
            raise PixelError("semantic", f"Missing bundled module #{mid}")

    def visit(mid, document, name, import_span=None):
        if mid in visiting:
            raise PixelError("semantic", f"Cyclic module import: {mid}", import_span)
        if mid in modules:
            return
        if len(visiting) > 128:
            raise PixelError("semantic", "Module nesting exceeds 128", import_span)
        visiting.add(mid)
        tree, space = frontend(document, name)
        for n in tree.data["body"]:
            if n.kind != "import":
                continue
            dep = n.data["module"]
            if resolver is None:
                raise PixelError("semantic", f"No resolver for module #{dep}", n.span)
            if dep in visiting:
                raise PixelError("semantic", f"Cyclic module import: {dep}", n.span)
            if dep in modules:
                continue
            try:
                dep_doc, dep_name = resolver(dep)
            except PixelError as e:
                raise PixelError(e.phase, e.message, n.span) from e
            visit(dep, dep_doc, dep_name, n.span)
        visiting.remove(mid)
        documents[str(mid)] = document
        modules[mid] = tree
        spaces[mid] = space

    try:
        visit("main", doc, source)
        Checker(modules).check()
        ir = lower(modules)
        if optimized:
            ir = optimize(ir)
        bytecode = assemble(ir)
        for name, fn in bytecode["functions"].items():
            document = documents[name.split(":")[0]]
            mapping = document.get("metadata", {}).get("source_map", {})
            if not isinstance(mapping, dict):
                continue
            for ins in fn["instructions"]:
                span = ins.get("span")
                if not span:
                    continue
                matches = [mapping.get(",".join(map(str, p))) for p in span["points"]]
                matches = [m for m in matches if valid_text_span(m)]
                if matches:
                    ins["span"] = {
                        **span,
                        "text": min(
                            matches,
                            key=lambda m: m["end"]["offset"] - m["start"]["offset"],
                        ),
                    }
        return Compilation(spaces["main"], modules["main"], modules, ir, bytecode, doc)
    except RecursionError as e:
        raise PixelError("semantic", "Program exceeds compiler nesting limit") from e


def compile_file(path, optimized=True):
    path = Path(path).resolve()
    if path.suffix.lower() == ".pxl":
        from .project import compile_project, read_project

        files, entry = read_project(path)
        return compile_project(files, entry, optimized)[1]
    root = path.parent

    def resolver(mid):
        target = root / f"{mid}.pixel"
        return codec.load(target), str(target)

    return compile_source(codec.load(path), str(path), resolver, optimized)


def normalize(doc, source="<source>"):
    # Normalization requires syntactic validity; imports need not be installed to format a module.
    tree, _ = frontend(doc, source)
    annotations = {
        k: v
        for k, v in doc.get("metadata", {}).items()
        if k in ("filename", "symbols", "imports")
    }
    result = canonical.encode(tree, annotations)
    if doc.get("bundle"):
        result["bundle"] = {
            **doc["bundle"],
            "modules": {
                mid: normalize(item, item.get("metadata", {}).get("filename", str(mid)))
                for mid, item in doc["bundle"].get("modules", {}).items()
            },
        }
    return result
