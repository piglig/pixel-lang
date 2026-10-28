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
        self.rows = []

    def row(self, *values, indent=0, x=None, y=None):
        x = indent * 2 if x is None else x
        y = self.y if y is None else y
        for i, value in enumerate(values):
            k, v = token(value)
            self.pixels.append({"position": [x + i * 2, y], "rgba": rgba(k, v)})
        if values:
            self.rows.append((indent, [(x + i * 2, y) for i in range(len(values))]))
        self.y = max(self.y, y + 2)
        return self

    def connect(self, kind, start, end):
        self.links.append({"kind": kind, "from": list(start), "to": list(end)})
        return self

    def build(self):
        # The builder's logical nesting is emitted as source links. Physical
        # coordinates never supply scope to the pixel reader.
        links = self.links.copy()
        explicit_children = {tuple(link['to']) for link in links if link['kind'] == 'child'}
        incoming = {tuple(link['to']) for link in links if link['kind'] == 'next'}
        parents = []
        for depth, positions in self.rows:
            starts = [position for position in positions if position not in incoming]
            head = starts[0] if len(starts) == 1 else positions[0]
            links.append(dict(kind='region', **{'from': list(head), 'to': list(head)}))
            while parents and parents[-1][0] >= depth:
                parents.pop()
            if parents and not explicit_children.intersection(positions):
                links.append(dict(kind='child', **{'from': list(parents[-1][1]), 'to': list(head)}))
            parents.append((depth, head))
        return new_source(
            max((p["position"][0] + 1 for p in self.pixels), default=1),
            max(1, self.y - 1),
            self.pixels.copy(),
            links,
        )
