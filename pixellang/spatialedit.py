"""Checked spatial editing proposals over an exact, immutable source snapshot."""

import hashlib
import json

from .compiler import frontend
from .model import Node, PixelError
from .project import compile_project
from .text import Source, lex, parse_text


def revision(files, entry):
    return hashlib.sha256(
        json.dumps([entry, files], sort_keys=True).encode()
    ).hexdigest()


class SpatialProject:
    @staticmethod
    def explain(node):
        if node.kind == "literal":
            typ = "string" if node.data["type"] == "str" else node.data["type"]
            return f"Produces a constant {typ} value."
        if node.kind == "binary":
            if node.data["op"] in ("and", "or"):
                return "Combines boolean values; the right operand runs only when needed to determine the result."
            return f"Evaluates the left operand, then the right, and applies {node.data['op']}."
        if node.kind == "let":
            return (
                "Initializes a mutable binding that can be reassigned."
                if node.data.get("mutable")
                else "Initializes a binding that cannot be reassigned; referenced objects can still be mutated."
            )
        return {
            "fn": "Declares a function with parameters, a result type and a body.",
            "lambda": "Creates a function value sharing its captured lexical bindings.",
            "return": "Returns a value and ends the current function call.",
            "print": "Evaluates a value and appends it to program output.",
            "if": "Chooses a branch using a boolean condition.",
            "while": "Repeats the body while its condition is true.",
            "for": "Visits a shallow snapshot of the collection in its iteration order.",
            "call": "Evaluates arguments in order and calls the resolved function.",
            "invoke": "Evaluates a function value, then its arguments, and calls it.",
            "variable": "Reads the value of the resolved lexical binding.",
            "field": "Reads a named field from the receiver.",
            "set": "Evaluates and assigns a new value to a mutable binding.",
            "match": "Selects an exhaustive variant arm and binds its payload values.",
            "record": "Declares a nominal record type and its named fields.",
            "enum": "Declares a tagged type with a fixed set of variants.",
        }.get(
            node.kind,
            f"{node.kind.replace('_', ' ').capitalize()} in the selected program structure.",
        )

    def __init__(self, files, entry="main.pxl"):
        self.files, self.entry = dict(files), entry
        self.revision = revision(files, entry)
        self.source, self.compiled = compile_project(files, entry)
        self.nodes, self.siblings = {}, {}
        self.documents = {}
        for document in [
            self.source,
            *self.source.get("bundle", {}).get("modules", {}).values(),
        ]:
            name = document.get("metadata", {}).get("filename")
            if name in files:
                self.documents[name] = document
                self.visit(parse_text(files[name], name), name)

    def visit(self, node, source, siblings=None):
        key = f"{source}:{node.span.start}:{node.span.end}:{node.kind}"
        self.nodes[key] = node
        if siblings is not None:
            self.siblings[key] = siblings
        for field, value in node.data.items():
            if field.startswith("_"):
                continue
            if isinstance(value, Node):
                self.visit(value, source)
            elif isinstance(value, list):
                for child in value:
                    if isinstance(child, Node):
                        self.visit(
                            child,
                            source,
                            value if field in ("body", "then", "else") else None,
                        )

    def describe(self, source):
        if source not in self.documents:
            raise PixelError(
                "studio", "Source module is not part of this executable project"
            )
        document = self.documents[source]
        _, space = frontend(document, source)
        mapping = document["metadata"].get("source_map", {})
        colors = {tuple(p["position"]): p["rgba"] for p in document["pixels"]}
        by_span, targets = {}, {}
        for key, node in self.nodes.items():
            if node.span.source != source or node.kind == "program":
                continue
            span = node.span
            by_span.setdefault((span.start, span.end), []).append(key)
            actions = []
            if node.kind == "literal":
                actions.append("literal")
            if node.kind == "binary":
                actions.append("operator")
            siblings = self.siblings.get(key)
            if siblings and node.kind not in ("fn", "record", "enum", "import", "case"):
                index = next(i for i, sibling in enumerate(siblings) if sibling is node)
                if index:
                    actions.append("up")
                if index + 1 < len(siblings):
                    actions.append("down")
            targets[key] = {
                "id": key,
                "kind": node.kind,
                "explanation": self.explain(node),
                "span": span.to_dict(),
                "text": self.files[source][span.start : span.end],
                "actions": actions,
            }
        for region in space["regions"]:
            keys = []
            for token in region["tokens"]:
                token["color"] = "#" + "".join(
                    f"{c:02x}" for c in colors[tuple(token["position"])][:3]
                )
                token["label"] = str(token["value"])
                span = mapping.get(",".join(map(str, token["position"])))
                token["targets"] = (
                    by_span.get((span["start"]["offset"], span["end"]["offset"]), [])
                    if span
                    else []
                )
                keys.extend(token["targets"])
            region["targets"] = list(dict.fromkeys(keys))
        return {
            "revision": self.revision,
            "source": source,
            "sources": sorted(self.documents),
            "regions": space["regions"],
            "targets": targets,
        }

    def edit(self, args):
        if args.get("revision") != self.revision:
            raise PixelError(
                "studio", "Source changed; refresh the spatial view before editing"
            )
        key, action = args.get("target"), args.get("action")
        node = self.nodes.get(key)
        if node is None:
            raise PixelError("studio", "Unknown spatial target")
        source, start, end = node.span.source, node.span.start, node.span.end
        target = self.describe(source)["targets"].get(key)
        if not target or action not in target["actions"]:
            raise PixelError(
                "studio", "This operation is not available for the selected region"
            )
        text = self.files[source]
        if action == "literal":
            replacement = args.get("value")
            if not isinstance(replacement, str) or len(replacement) > 100_000:
                raise PixelError("studio", "Enter a PixelLang literal")
            tree = parse_text("fn main() { print(" + replacement + ") }")
            if len(tree.data["body"]) != 1:
                raise PixelError("studio", "Replacement must be a single literal")
            body = tree.data["body"][0].data["body"]
            value = (
                body[0].data.get("value")
                if len(body) == 1 and body[0].kind == "print"
                else None
            )
            if value is None or not (
                value.kind == "literal"
                or value.kind == "unary"
                and value.data["op"] == "-"
                and value.data["operand"].kind == "literal"
            ):
                raise PixelError("studio", "Replacement must be a single literal")
        elif action == "operator":
            operator = args.get("value")
            if operator not in (
                "+",
                "-",
                "*",
                "/",
                "%",
                "==",
                "!=",
                "<",
                "<=",
                ">",
                ">=",
                "&&",
                "||",
            ):
                raise PixelError("studio", "Choose a supported binary operator")
            left, right = node.data["left"].span, node.data["right"].span
            gap = text[left.end : right.start]
            original = {"and": "&&", "or": "||"}.get(node.data["op"], node.data["op"])
            token = next(t for t in lex(Source(gap, source)) if t.value == original)
            gap = gap[: token.start] + operator + gap[token.end :]
            replacement = (
                "("
                + text[start : left.start]
                + "("
                + text[left.start : left.end]
                + ")"
                + gap
                + "("
                + text[right.start : right.end]
                + ")"
                + text[right.end : end]
                + ")"
            )
        else:
            siblings = self.siblings[key]
            index = next(i for i, sibling in enumerate(siblings) if sibling is node)
            other = siblings[index + (-1 if action == "up" else 1)]
            first, second = sorted((node, other), key=lambda n: n.span.start)
            start, end = first.span.start, second.span.end
            replacement = (
                text[second.span.start : second.span.end]
                + text[first.span.end : second.span.start]
                + text[first.span.start : first.span.end]
            )
        proposed = {**self.files, source: text[:start] + replacement + text[end:]}
        # Proposals never mutate buffers or execute a program. The entire project
        # must survive pixel encoding and checking before the host applies an edit.
        compile_project(proposed, self.entry)
        return {
            "revision": self.revision,
            "source": source,
            "start": start,
            "end": end,
            "text": replacement,
            "files": proposed,
        }
