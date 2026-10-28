"""Persistent insertion-ordered maps with a radix-32 hash index.

Hash layout is internal and never affects iteration or serialized program order.
Deleted order slots are compacted once they dominate the live entries.
"""
from collections.abc import Mapping
from dataclasses import dataclass, field
from .value_vector import ValueVector


@dataclass(frozen=True, slots=True)
class _Leaf:
    hash: int
    entries: tuple


@dataclass(frozen=True, slots=True)
class _Branch:
    bitmap: int
    children: tuple


def _hash(key):
    return hash(key) & ((1 << 64) - 1)


def _find(node, hashed, key, shift=0):
    if node is None:
        raise KeyError(key)
    if isinstance(node, _Leaf):
        if node.hash == hashed:
            for stored, index in node.entries:
                if stored == key:
                    return index
        raise KeyError(key)
    bit = 1 << ((hashed >> shift) & 31)
    if not node.bitmap & bit:
        raise KeyError(key)
    return _find(node.children[(node.bitmap & (bit - 1)).bit_count()], hashed, key, shift + 5)


def _join(left, right, shift):
    a, b = (left.hash >> shift) & 31, (right.hash >> shift) & 31
    if a == b:
        return _Branch(1 << a, (_join(left, right, shift + 5),))
    return _Branch((1 << a) | (1 << b), (left, right) if a < b else (right, left))


def _insert(node, hashed, key, index, shift=0):
    if node is None:
        return _Leaf(hashed, ((key, index),))
    if isinstance(node, _Leaf):
        if node.hash == hashed:
            return _Leaf(hashed, (*node.entries, (key, index)))
        return _join(node, _Leaf(hashed, ((key, index),)), shift)
    bit = 1 << ((hashed >> shift) & 31)
    slot = (node.bitmap & (bit - 1)).bit_count()
    if node.bitmap & bit:
        child = _insert(node.children[slot], hashed, key, index, shift + 5)
        children = (*node.children[:slot], child, *node.children[slot + 1:])
    else:
        children = (*node.children[:slot], _Leaf(hashed, ((key, index),)), *node.children[slot:])
    return _Branch(node.bitmap | bit, children)


def _remove(node, hashed, key, shift=0):
    if isinstance(node, _Leaf):
        entries = tuple(pair for pair in node.entries if pair[0] != key)
        return _Leaf(hashed, entries) if entries else None
    bit = 1 << ((hashed >> shift) & 31)
    slot = (node.bitmap & (bit - 1)).bit_count()
    child = _remove(node.children[slot], hashed, key, shift + 5)
    if child is None:
        children = (*node.children[:slot], *node.children[slot + 1:])
        bitmap = node.bitmap & ~bit
    else:
        children = (*node.children[:slot], child, *node.children[slot + 1:])
        bitmap = node.bitmap
    if not children:
        return None
    if len(children) == 1 and isinstance(children[0], _Leaf):
        return children[0]
    return _Branch(bitmap, children)


@dataclass(frozen=True, slots=True, eq=False)
class ValueMap(Mapping):
    _root: object = None
    _order: ValueVector = field(default_factory=ValueVector)
    _size: int = 0

    @classmethod
    def from_mapping(cls, values):
        if isinstance(values, cls):
            return values
        result = cls()
        for key, value in values.items():
            result = result.set(key, value)
        return result

    def __len__(self):
        return self._size

    def __iter__(self):
        return (pair[0] for pair in self._order if pair is not None)

    def __getitem__(self, key):
        return self._order[_find(self._root, _hash(key), key)][1]

    def items(self):
        return (pair for pair in self._order if pair is not None)

    def values(self):
        return (pair[1] for pair in self._order if pair is not None)

    def copy(self):
        return dict(self.items())

    def set(self, key, value):
        hashed = _hash(key)
        try:
            index = _find(self._root, hashed, key)
        except KeyError:
            return ValueMap(_insert(self._root, hashed, key, len(self._order)),
                            self._order.append((key, value)), self._size + 1)
        stored = self._order[index][0]
        return ValueMap(self._root, self._order.set(index, (stored, value)), self._size)

    def remove(self, key):
        hashed = _hash(key)
        try:
            index = _find(self._root, hashed, key)
        except KeyError:
            return self
        size = self._size - 1
        if not size:
            return ValueMap()
        result = ValueMap(_remove(self._root, hashed, key), self._order.set(index, None), size)
        if len(result._order) > max(32, 2 * size):
            compact = ValueMap()
            for stored, value in result.items():
                compact = compact.set(stored, value)
            return compact
        return result
