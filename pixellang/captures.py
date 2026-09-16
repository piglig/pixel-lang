"""Lexical binding identities and transitive closure capture requirements.

This pass works on syntax, before VM slots exist. It never copies captured values.
Its stable integer identities distinguish declarations with equal source names.
The checker remains responsible for type and definite-assignment diagnostics.
"""

from dataclasses import dataclass, field

from .model import Node


@dataclass
class Binding:
    identity: int
    name: str | int
    owner: int
    mutable: bool
    declaration: Node
    parameter: dict | None = None
    captured: bool = False


@dataclass
class FunctionScope:
    identity: int
    node: Node
    captures: list[int] = field(default_factory=list)


@dataclass
class CapturePlan:
    bindings: list[Binding] = field(default_factory=list)
    functions: list[FunctionScope] = field(default_factory=list)
    references: list[tuple[Node, int]] = field(default_factory=list)


def analyze_captures(tree):
    plan = CapturePlan()
    scopes, functions = [], []

    def bind(name, node, mutable=False, parameter=None):
        binding = Binding(
            len(plan.bindings), name, functions[-1].identity, mutable, node, parameter
        )
        plan.bindings.append(binding)
        scopes[-1][name] = binding

    def reference(node, name):
        binding = next(
            (scope[name] for scope in reversed(scopes) if name in scope), None
        )
        if binding is None:
            return  # Module functions and unresolved names are checked separately.
        plan.references.append((node, binding.identity))
        for function in reversed(functions):
            if function.identity == binding.owner:
                break
            if binding.identity not in function.captures:
                function.captures.append(binding.identity)
            binding.captured = True

    def block(nodes):
        scopes.append({})
        for node in nodes:
            visit(node)
        scopes.pop()

    def function(node):
        scope = FunctionScope(len(plan.functions), node)
        plan.functions.append(scope)
        functions.append(scope)
        scopes.append({})
        for parameter in node.data["params"]:
            bind(parameter["name"], node, parameter=parameter)
        block(node.data["body"])
        scopes.pop()
        functions.pop()

    def visit(node):
        d, kind = node.data, node.kind
        if kind in ("fn", "lambda"):
            function(node)
        elif kind == "let":
            visit(d["value"])
            bind(d["name"], node, d.get("mutable", False))
        elif kind in ("variable", "set"):
            reference(node, d["name"])
            if kind == "set":
                visit(d["value"])
        elif kind in ("call", "function_value"):
            if d.get("module") is None:
                reference(node, d["name"])
            else:
                reference(node, d.get("_module_binding", d["module"]))
            for argument in d.get("args", []):
                visit(argument)
        elif kind == "for":
            visit(d["value"])
            scopes.append({})
            bind(d["name"], node)
            if "value_name" in d:
                bind(d["value_name"], node)
            block(d["body"])
            scopes.pop()
        elif kind == "try":
            block(d["body"])
            scopes.append({})
            bind(d["name"], node)
            block(d["otherwise"])
            scopes.pop()
        elif kind == "case":
            scopes.append({})
            for parameter in d["params"]:
                bind(parameter["name"], node, parameter=parameter)
            if "body" in d:
                block(d["body"])
            else:
                visit(d["value"])
            scopes.pop()
        else:
            for key, value in d.items():
                if key.startswith("_"):
                    continue
                if isinstance(value, Node):
                    visit(value)
                elif isinstance(value, list):
                    nodes = [item for item in value if isinstance(item, Node)]
                    if key in ("body", "otherwise"):
                        block(nodes)
                    else:
                        for child in nodes:
                            visit(child)

    for node in tree.data["body"]:
        if node.kind == "fn":
            function(node)
    return plan
