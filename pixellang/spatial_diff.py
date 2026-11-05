"""Identity-based snapshot data differences, pruning shared storage branches.

Rows are ordered by business key, independently of storage layout. Version numbers,
source provenance and table iteration order are not object-data changes.
"""
from dataclasses import dataclass
from .spatial_values import SpaceValue, SpatialError


@dataclass(frozen=True, slots=True)
class Change:
    key: str
    before: object
    after: object
    fields: tuple[str, ...]

    @property
    def kind(self):
        return 'added' if self.before is None else 'removed' if self.after is None else 'updated'


@dataclass(frozen=True, slots=True)
class SpaceDiff:
    entities: tuple[Change, ...]
    links: tuple[Change, ...]
    visited_nodes: int
    compared_entries: int

    @property
    def empty(self):
        return not self.entities and not self.links


def _candidates(before, after):
    keys = set()
    visited = 0

    def visit(left, left_shift, right, right_shift):
        nonlocal visited
        if left is right and left_shift == right_shift:
            return
        visited += 1
        if left_shift < right_shift:
            visit(left, left_shift, right[0] if right else (), right_shift - 5)
            for child in right[1:]:
                visit((), right_shift - 5, child, right_shift - 5)
        elif left_shift > right_shift:
            visit(left[0] if left else (), left_shift - 5, right, right_shift)
            for child in left[1:]:
                visit(child, left_shift - 5, (), left_shift - 5)
        elif left_shift:
            for index in range(max(len(left), len(right))):
                visit(left[index] if index < len(left) else (), left_shift - 5,
                      right[index] if index < len(right) else (), right_shift - 5)
        else:
            for index in range(max(len(left), len(right))):
                old = left[index] if index < len(left) else None
                new = right[index] if index < len(right) else None
                if old is new:
                    continue
                if old is not None: keys.add(old[0])
                if new is not None: keys.add(new[0])

    old, new = before._data._order, after._data._order
    visit(old._root, old._shift, new._root, new._shift)
    return keys, visited


def _changes(before, after, fields):
    keys, visited = _candidates(before, after)
    changes = []
    for key in sorted(keys):
        old, new = before.get(key), after.get(key)
        if old is new:
            continue
        changed = fields if old is None or new is None else tuple(
            field for field in fields if getattr(old, field) != getattr(new, field))
        if changed:
            changes.append(Change(key, old, new, changed))
    return tuple(changes), visited, len(keys)


def compare_spaces(before, after):
    """Compare states of the same typed space, including independently forked states."""
    if not isinstance(before, SpaceValue) or not isinstance(after, SpaceValue):
        raise SpatialError('Snapshot comparison requires two spaces')
    if ((before.identity, before.frame, before.payload_type, before.link_type) !=
            (after.identity, after.frame, after.payload_type, after.link_type)):
        raise SpatialError('Snapshot comparison requires the same typed space identity')
    entities, entity_nodes, entity_entries = _changes(before.entities, after.entities, ('position', 'payload'))
    links, link_nodes, link_entries = _changes(before.links, after.links, ('source', 'target', 'payload'))
    return SpaceDiff(entities, links, entity_nodes + link_nodes, entity_entries + link_entries)
