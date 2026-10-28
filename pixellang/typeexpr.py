"""Immutable, compositional types shared by checking, encoding and tools.

The internal spelling is deliberately unambiguous: container suffixes belong to
the complete preceding type, and function results have explicit parentheses.
Authoring syntax and pixel tokens are separate renderings of this structure.
"""

import re
from dataclasses import dataclass
from functools import lru_cache

SCALARS = frozenset({"int", "float64", "bool", "str", "json", "unit"})
SPATIAL_TYPES = frozenset({'Point3', 'Vec3', 'Transform3', 'Entity', 'Space', 'Link'})
MAX_TYPE_DEPTH = 32
MAX_TYPE_NODES = 256
MAX_TYPE_TEXT = 4096


@dataclass(frozen=True)
class Type:
    kind: str
    name: str = ""
    args: tuple["Type", ...] = ()

    def spelling(self):
        if self.kind == "inout":
            return "inout<" + self.args[0].spelling() + ">"
        if self.kind == "array":
            return self.args[0].spelling() + "[]"
        if self.kind == "map":
            if len(self.args) != 2:
                raise ValueError("Map types require key and value types")
            return "map<" + ",".join(a.spelling() for a in self.args) + ">"
        if self.kind == "function":
            if self.name not in ("", "effect"):
                raise ValueError("Unknown function effect contract")
            return (
                ("effect_fn(" if self.name == "effect" else "fn(")
                + ",".join(a.spelling() for a in self.args[:-1])
                + ")->("
                + self.args[-1].spelling()
                + ")"
            )
        if self.kind == "parameter":
            return "$" + self.name
        return self.name + (
            "<" + ",".join(a.spelling() for a in self.args) + ">" if self.args else ""
        )

    def walk(self):
        yield self
        for arg in self.args:
            yield from arg.walk()

    def substitute(self, bindings):
        if self.kind == "parameter":
            return bindings.get(self.name, self)
        return Type(
            self.kind, self.name, tuple(a.substitute(bindings) for a in self.args)
        )

    def display(self, name_of=lambda name: name):
        if self.kind == "inout":
            return "inout " + self.args[0].display(name_of)
        if self.kind == "array":
            return "[" + self.args[0].display(name_of) + "]"
        if self.kind == "map":
            if len(self.args) != 2:
                raise ValueError("Map types require key and value types")
            return "map[" + ", ".join(a.display(name_of) for a in self.args) + "]"
        if self.kind == "function":
            if self.name not in ("", "effect"):
                raise ValueError("Unknown function effect contract")
            return (
                ("effect fn(" if self.name == "effect" else "fn(")
                + ", ".join(a.display(name_of) for a in self.args[:-1])
                + ")"
                + (
                    ""
                    if self.args[-1] == Type("scalar", "unit")
                    else " -> " + self.args[-1].display(name_of)
                )
            )
        name = "string" if self.name == "str" else name_of(self.name)
        return name + (
            "[" + ", ".join(a.display(name_of) for a in self.args) + "]"
            if self.args
            else ""
        )


class TypeParser:
    def __init__(self, text):
        if not isinstance(text, str) or not 1 <= len(text) <= MAX_TYPE_TEXT:
            raise ValueError("Invalid type spelling length")
        self.text, self.pos, self.nodes = text, 0, 0

    def take(self, token):
        if self.text.startswith(token, self.pos):
            self.pos += len(token)
            return True
        return False

    def need(self, token):
        if not self.take(token):
            raise ValueError(f"Expected {token} in type")

    def sequence(self, end, depth):
        args = []
        if not self.take(end):
            while True:
                args.append(self.type(depth + 1))
                if self.take(end):
                    break
                self.need(",")
        return tuple(args)

    def type(self, depth=0):
        self.nodes += 1
        if depth > MAX_TYPE_DEPTH or self.nodes > MAX_TYPE_NODES:
            raise ValueError("Type complexity limit exceeded")
        effect = self.take("effect_fn(")
        if effect or self.take("fn("):
            params = self.sequence(")", depth)
            self.need("->(")
            result = self.type(depth + 1)
            self.need(")")
            value = Type("function", name="effect" if effect else "", args=params + (result,))
        else:
            match = re.match(
                r"@[0-9]{1,5}:[0-9]{1,5}|@[\w]+(?:\.[\w]+)?|\$[\w]+|[A-Za-z_][A-Za-z_0-9]*",
                self.text[self.pos :],
            )
            if match is None:
                raise ValueError("Expected type name")
            name = match[0]
            self.pos += len(name)
            if name.startswith("$"):
                value = Type("parameter", name[1:])
            elif name in SCALARS:
                value = Type("scalar", name)
            elif name == "inout":
                self.need("<")
                args = self.sequence(">", depth)
                if len(args) != 1:
                    raise ValueError("Inout mode requires one value type")
                value = Type("inout", args=args)
            elif name == "map":
                self.need("<")
                args = self.sequence(">", depth)
                if len(args) != 2:
                    raise ValueError("Map type requires key and value types")
                value = Type("map", args=args)
            elif name in SPATIAL_TYPES or name in ("Error", "Option", "Result", "Flow") or name.startswith("@"):
                if (
                    name.startswith("@")
                    and ":" in name
                    and any(int(n) > 65535 for n in name[1:].split(":"))
                ):
                    raise ValueError("Nominal type identity exceeds pixel range")
                args = self.sequence(">", depth) if self.take("<") else ()
                value = Type("flow_handle" if name == "Flow" else "spatial" if name in SPATIAL_TYPES else "nominal", name, args)
            else:
                raise ValueError("Unknown type name")
        while True:
            if self.take("[]"):
                value = Type("array", args=(value,))
            else:
                break
            self.nodes += 1
            depth += 1
            if depth > MAX_TYPE_DEPTH or self.nodes > MAX_TYPE_NODES:
                raise ValueError("Type complexity limit exceeded")
        return value


@lru_cache(maxsize=4096)
def parse_type(text):
    parser = TypeParser(text)
    result = parser.type()
    if parser.pos != len(text):
        raise ValueError("Unexpected text after type")
    pending = [(result, 0)]
    while pending:
        node, depth = pending.pop()
        if depth > MAX_TYPE_DEPTH:
            raise ValueError("Type complexity limit exceeded")
        pending.extend((arg, depth + 1) for arg in node.args)
    return result


def is_parameter_type(value, parameters=frozenset()):
    if value.kind == "inout":
        return len(value.args) == 1 and is_value_type(value.args[0], parameters)
    return is_value_type(value, parameters)


def is_value_type(value, parameters=frozenset()):
    """Structural validity; declaration arity/access are checked by the checker."""
    if value.kind == "inout":
        return False
    if value.kind == "scalar":
        return value.name != "unit"
    if value.kind == "parameter":
        return value.name in parameters
    if value.kind == "map":
        return len(value.args) == 2 and all(is_value_type(a, parameters) for a in value.args)
    if value.kind == 'flow_handle':
        return len(value.args) == 1 and value.args[0].kind == 'function' and is_value_type(value.args[0], parameters)
    if value.kind == 'spatial':
        arity = 1 if value.name in ('Point3', 'Vec3') else 3 if value.name in ('Space', 'Link') else 2
        return len(value.args) == arity and all(is_value_type(a, parameters) for a in value.args)
    if value.kind == "function":
        return value.name in ("", "effect") and bool(value.args) and all(is_parameter_type(a, parameters) for a in value.args[:-1]) and (
            value.args[-1] == Type("scalar", "unit")
            or is_value_type(value.args[-1], parameters)
        )
    if value.kind == "nominal":
        if value.name == "Error" and value.args:
            return False
        if value.name == "Option" and len(value.args) != 1:
            return False
        if value.name == "Result" and len(value.args) != 2:
            return False
    return value.kind in ("array", "nominal") and (value.kind != "array" or len(value.args) == 1) and all(is_value_type(a, parameters) for a in value.args)


@lru_cache(maxsize=4096)
def array_item(spelling):
    typ = parse_type(spelling)
    return typ.args[0].spelling() if typ.kind == 'array' else None


@lru_cache(maxsize=4096)
def is_typed_map(spelling):
    """Identify the outer map constructor, not an array containing maps."""
    typ = parse_type(spelling)
    return typ.kind == 'map' and len(typ.args) == 2


@lru_cache(maxsize=4096)
def map_parts(spelling):
    """Return explicit key/value spellings, or None for a non-map type."""
    typ = parse_type(spelling)
    if typ.kind != 'map':
        return None
    return tuple(arg.spelling() for arg in typ.args)


def is_assignable(expected: Type, actual: Type) -> bool:
    """Whether a callable can satisfy a required signature without conversion.

    Pure fn can satisfy effect_fn; the reverse is forbidden. Parameters are
    contravariant and results covariant. All non-callable types remain invariant.
    This relation assumes valid resolved types; body checking is separate.
    """
    if expected == actual:
        return True
    if expected.kind != "function" or actual.kind != "function":
        return False
    if (expected.name not in ("", "effect")
            or actual.name not in ("", "effect")
            or not expected.args or len(expected.args) != len(actual.args)):
        return False
    if expected.name != "effect" and actual.name == "effect":
        return False
    return (
        all(is_assignable(given, required)
            for required, given in zip(expected.args[:-1], actual.args[:-1]))
        and is_assignable(expected.args[-1], actual.args[-1])
    )
