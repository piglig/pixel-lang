"""Explicit semantic regions and parent relationships."""

from itertools import pairwise

from .model import PixelError, Region, Span


def analyze(tokens, links):
    by_pos = {t.position: t for t in tokens}
    for link in links:
        if tuple(link["from"]) not in by_pos or tuple(link["to"]) not in by_pos:
            p = tuple(link["from"])
            raise PixelError(
                "spatial",
                "Connection endpoint is not a semantic pixel",
                Span(tokens[0].span.source if tokens else "<source>", (p,)),
            )
    regions, position_region = explicit_regions(tokens, links, by_pos)
    explicit = set()
    for e in links:
        if e["kind"] != "child":
            continue
        parent, child = (
            position_region[tuple(e["from"])],
            position_region[tuple(e["to"])],
        )
        if parent >= child or child in explicit:
            raise PixelError(
                "spatial",
                "Child must follow one unique parent region",
                regions[child].span,
            )
        regions[child].parent = parent
        explicit.add(child)
    for r in regions:
        if r.parent is not None:
            parent = regions[r.parent]
            head = parent.tokens[0].value
            index = 0
            while head in ("export", "entry") and index + 1 < len(parent.tokens):
                index += 1
                head = parent.tokens[index].value
            if head not in (
                "if",
                "else",
                "while",
                "fn",
                "effect_fn",
                "flow",
                "lambda_body",
                "effect_body",
                "value_body",
                "for",
                "match",
                "case",
            ):
                raise PixelError(
                    "spatial",
                    "Nested region requires a condition, loop or function parent",
                    r.span,
                )
    return regions


def describe(regions, links):
    return {
        "dimensions": 2,
        "regions": [
            {
                "id": r.id,
                "parent": r.parent,
                "span": r.span.to_dict(),
                "tokens": [
                    {"kind": t.kind, "value": t.value, "position": list(t.position)}
                    for t in r.tokens
                ],
            }
            for r in regions
        ],
        "connections": links,
    }


def explicit_regions(tokens, links, by_pos):
    heads, nxt, incoming, explicit_rows = set(), {}, set(), set()
    def fail(message, position=()):
        raise PixelError("spatial", message, Span(tokens[0].span.source if tokens else "<source>", (position,) if position else ()))
    for link in links:
        a, b = tuple(link["from"]), tuple(link["to"])
        if link["kind"] == "region":
            if a != b or a in heads:
                fail("Region marker must uniquely point to itself", a)
            heads.add(a)
        elif link["kind"] == "next":
            if a[1] != b[1] or a in nxt or b in incoming:
                fail("Invalid explicit region chain", a)
            nxt[a] = b
            incoming.add(b)
            explicit_rows.add(a[1])
        elif link["kind"] != "child":
            fail("Unknown connection kind", a)
    ordered_tokens = sorted(tokens, key=lambda t: (t.position[1], t.position[0]))
    for left, right in pairwise(ordered_tokens):
        a, b = left.position, right.position
        if a[1] == b[1] and a[1] not in explicit_rows and b not in heads and b[0] - a[0] <= 3:
            nxt[a] = b
            incoming.add(b)
    regions, assigned = [], {}
    for head in sorted(heads, key=lambda p: (p[1], p[0])):
        if head in incoming:
            fail("Region head cannot have an incoming next link", head)
        ordered, cursor = [], head
        while cursor is not None:
            if cursor in assigned:
                fail("Cyclic or overlapping explicit regions", cursor)
            assigned[cursor] = len(regions)
            ordered.append(by_pos[cursor])
            cursor = nxt.get(cursor)
        regions.append(Region(len(regions), ordered, Span.merge(*(t.span for t in ordered))))
    if len(assigned) != len(tokens):
        fail("Explicit regions must cover every semantic pixel")
    return regions, assigned
