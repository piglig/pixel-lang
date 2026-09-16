"""Region detection and explicit/default spatial relationships."""

from collections import defaultdict
from itertools import pairwise

from .model import PixelError, Region, Span


def analyze(tokens, links):
    rows = defaultdict(list)
    for t in tokens:
        rows[t.position[1]].append(t)
    by_pos = {t.position: t for t in tokens}
    for link in links:
        if tuple(link["from"]) not in by_pos or tuple(link["to"]) not in by_pos:
            p = tuple(link["from"])
            raise PixelError(
                "spatial",
                "Connection endpoint is not a semantic pixel",
                Span(tokens[0].span.source if tokens else "<source>", (p,)),
            )
    marked = any(link["kind"] == "region" for link in links)
    if marked:
        regions, position_region = explicit_regions(tokens, links, by_pos)
    else:
        regions, position_region = [], {}
        for y, row in sorted(rows.items()):
            row.sort(key=lambda t: t.position[0])
            span = Span.merge(*(t.span for t in row))
            edges = [e for e in links if e["kind"] == "next" and e["from"][1] == y]
            if edges:
                nxt, incoming = {}, set()
                for e in edges:
                    a, b = tuple(e["from"]), tuple(e["to"])
                    if b[1] != y or a in nxt or b in incoming:
                        raise PixelError(
                            "spatial",
                            "Next links must form one unbranched chain within a region",
                            span,
                        )
                    nxt[a] = b
                    incoming.add(b)
                starts = [t.position for t in row if t.position not in incoming]
                if len(starts) != 1:
                    raise PixelError(
                        "spatial", "Next links contain a cycle or disconnected chain", span
                    )
                order, seen, p = [], set(), starts[0]
                while p is not None:
                    if p in seen:
                        raise PixelError("spatial", "Cyclic next connection", span)
                    seen.add(p)
                    order.append(by_pos[p])
                    p = nxt.get(p)
                if len(order) != len(row):
                    raise PixelError(
                        "spatial",
                        "Explicit next chain must cover every token in the region",
                        span,
                    )
                row = order
            elif any(b.position[0] - a.position[0] > 3 for a, b in pairwise(row)):
                raise PixelError(
                    "spatial",
                    "Disconnected row: gap exceeds two empty cells; add explicit next connections",
                    span,
                )
            region = Region(len(regions), row, min(t.position[0] for t in row), span)
            regions.append(region)
            for t in row:
                position_region[t.position] = region.id
    if not marked:
        stack = []
        for r in regions:
            while stack and regions[stack[-1]].indent >= r.indent:
                stack.pop()
            r.parent = stack[-1] if stack else None
            stack.append(r.id)
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
        regions[child].explicit_parent = True
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
                "lambda_body",
                "try",
                "catch",
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
                "explicit_parent": r.explicit_parent,
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
        regions.append(Region(len(regions), ordered, head[0], Span.merge(*(t.span for t in ordered))))
    if len(assigned) != len(tokens):
        fail("Explicit regions must cover every semantic pixel")
    return regions, assigned
