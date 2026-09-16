"""Convenient programmatic pixel creation, not a text-language compiler."""

from .codec import KEYWORDS, OPS, PUNCT, TYPES, new_source, rgba


def I(name):
    return ("id", name)


def token(value):
    if isinstance(value, tuple):
        return value
    if type(value) is bool:
        return ("bool", value)
    if type(value) is int:
        return ("int", value)
    for kind, values in [
        ("op", OPS),
        ("keyword", KEYWORDS),
        ("punct", PUNCT),
        ("type", TYPES),
    ]:
        if value in values:
            return kind, value
    raise ValueError(f"Not a semantic token: {value}")


class Grid:
    def __init__(self):
        self.pixels, self.links, self.y = [], [], 0

    def row(self, *values, indent=0, x=None, y=None):
        x = indent * 2 if x is None else x
        y = self.y if y is None else y
        for i, value in enumerate(values):
            k, v = token(value)
            self.pixels.append({"position": [x + i * 2, y], "rgba": rgba(k, v)})
        self.y = max(self.y, y + 2)
        return self

    def connect(self, kind, start, end):
        self.links.append({"kind": kind, "from": list(start), "to": list(end)})
        return self

    def build(self):
        return new_source(
            max((p["position"][0] + 1 for p in self.pixels), default=1),
            max(1, self.y - 1),
            self.pixels.copy(),
            self.links.copy(),
        )
