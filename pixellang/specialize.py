"""Structural specialization of checked generic function syntax."""

from copy import deepcopy

from .model import Node
from .typeexpr import parse_type


def function_instance(template, arguments):
    result = deepcopy(template)
    bindings = {
        str(p["name"]): argument
        for p, argument in zip(template.data["type_params"], arguments)
    }

    def visit(value):
        if isinstance(value, Node):
            visit(value.data)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            for key, item in list(value.items()):
                if key in (
                    "type",
                    "record_type",
                    "enum_type",
                    "_declared_type",
                ) and isinstance(item, str):
                    value[key] = parse_type(item).substitute(bindings).spelling()
                elif key == "type_args":
                    value[key] = [
                        parse_type(t).substitute(bindings).spelling() for t in item
                    ]
                elif not key.startswith("_"):
                    visit(item)

    visit(result)
    result.data.pop("type_params")
    return result
