"""Immutable radix-32 storage for ordinary array values.

VM references inside leaves remain ordinary GC edges. The VM charges shared tree
slots once while live, including internal pointers, and releases them by ownership.
"""
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import islice
import struct

_BITS = 5
_WIDTH = 1 << _BITS
_MASK = _WIDTH - 1


@dataclass(frozen=True, slots=True)
class _IntegerLeaf(Sequence):
    data: bytes
    width: int

    def __len__(self):
        return len(self.data) // self.width

    def __getitem__(self, index):
        if isinstance(index, slice):
            return tuple(self)[index]
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError('integer leaf index out of range')
        return self.data[index] if self.width == 1 else struct.unpack_from('<q', self.data, index * 8)[0]

    def __iter__(self):
        if self.width == 1:
            return iter(self.data)
        return (item[0] for item in struct.iter_unpack('<q', self.data))


def _pack(values):
    values = tuple(values)
    if not values:
        return ()
    if all(type(value) is int and 0 <= value <= 255 for value in values):
        return _IntegerLeaf(bytes(values), 1)
    return _IntegerLeaf(struct.pack('<' + 'q' * len(values), *values), 8)


def _leaf_edit(node, index, values, replace_count):
    values = tuple(values)
    width = node.width if isinstance(node, _IntegerLeaf) else 1
    if width == 1 and any(not 0 <= value <= 255 for value in values):
        width = 8
    if isinstance(node, _IntegerLeaf) and node.width == width:
        data = node.data
    else:
        data = bytes(node) if width == 1 else struct.pack('<' + 'q' * len(node), *node)
    inserted = bytes(values) if width == 1 else struct.pack('<' + 'q' * len(values), *values)
    return _IntegerLeaf(data[:index * width] + inserted + data[(index + replace_count) * width:], width)


def _insert(node, shift, index, value, append, leaf_bits):
    if shift == 0:
        if leaf_bits == 8:
            return _leaf_edit(node, index & 255, (value,), 0 if append else 1)
        if append:
            return (*node, value)
        return (*node[:index & _MASK], value, *node[(index & _MASK) + 1:])
    slot = (index >> (shift + leaf_bits - _BITS)) & _MASK
    child = node[slot] if slot < len(node) else ()
    changed = _insert(child, shift - _BITS, index, value, append, leaf_bits)
    return (*node[:slot], changed, *node[slot + 1:])


def _remove_last(node, shift):
    if shift == 0:
        if isinstance(node, _IntegerLeaf):
            return _IntegerLeaf(node.data[:-node.width], node.width)
        return node[:-1]
    changed = _remove_last(node[-1], shift - _BITS)
    return (*node[:-1], changed) if changed else node[:-1]


def _extend_leaf(node, shift, index, values, leaf_bits):
    """Append a chunk fitting in one leaf, copying its ancestor path once."""
    if shift == 0:
        if leaf_bits == 8:
            return _leaf_edit(node, len(node), values, 0)
        return (*node, *values)
    slot = (index >> (shift + leaf_bits - _BITS)) & _MASK
    child = node[slot] if slot < len(node) else ()
    changed = _extend_leaf(child, shift - _BITS, index, values, leaf_bits)
    return (*node[:slot], changed, *node[slot + 1:])


def _values(node, shift):
    if shift == 0:
        yield from node
    else:
        for child in node:
            yield from _values(child, shift - _BITS)


@dataclass(frozen=True, slots=True, eq=False)
class ValueVector(Sequence):
    _root: tuple = ()
    _shift: int = 0
    _size: int = 0
    _leaf_bits: int = _BITS

    @classmethod
    def from_sequence(cls, values, *, packed=False):
        if isinstance(values, cls) and (not packed or values._leaf_bits == 8):
            return values
        leaf_bits = 8 if packed else _BITS
        width = 1 << leaf_bits
        leaf = _pack if packed else tuple
        level = tuple(leaf(values[start:start + width]) for start in range(0, len(values), width))
        if not level:
            return cls(_leaf_bits=leaf_bits)
        shift = 0
        while len(level) > 1:
            level = tuple(level[start:start + _WIDTH] for start in range(0, len(level), _WIDTH))
            shift += _BITS
        return cls(level[0], shift, len(values), leaf_bits)

    def __len__(self):
        return self._size

    def __iter__(self):
        return _values(self._root, self._shift)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(self._size))]
        if index < 0:
            index += self._size
        if not 0 <= index < self._size:
            raise IndexError('array index out of range')
        node = self._root
        for shift in range(self._shift, 0, -_BITS):
            node = node[(index >> (shift + self._leaf_bits - _BITS)) & _MASK]
        return node[index & ((1 << self._leaf_bits) - 1)]

    def __eq__(self, other):
        return isinstance(other, Sequence) and len(self) == len(other) and all(a == b for a, b in zip(self, other))

    def copy(self):
        # Legacy mutable graph conversion needs independently writable storage.
        return list(self)

    def __mul__(self, count):
        return list(self) * count

    def set(self, index, value):
        if not 0 <= index < self._size:
            raise IndexError('array index out of range')
        return ValueVector(_insert(self._root, self._shift, index, value, False, self._leaf_bits), self._shift, self._size, self._leaf_bits)

    def append(self, value):
        root, shift = self._root, self._shift
        if self._size == 1 << (shift + self._leaf_bits):
            root, shift = (root,), shift + _BITS
        return ValueVector(_insert(root, shift, self._size, value, True, self._leaf_bits), shift, self._size + 1, self._leaf_bits)

    def extend(self, values):
        source = iter(values)
        root, shift, size = self._root, self._shift, self._size
        while chunk := tuple(islice(source, (1 << self._leaf_bits) - (size & ((1 << self._leaf_bits) - 1)))):
            if size == 1 << (shift + self._leaf_bits):
                root, shift = (root,), shift + _BITS
            root = _extend_leaf(root, shift, size, chunk, self._leaf_bits)
            size += len(chunk)
        return self if size == self._size else ValueVector(root, shift, size, self._leaf_bits)

    def pop(self):
        if not self._size:
            raise IndexError('pop from empty array')
        root, shift = _remove_last(self._root, self._shift), self._shift
        if shift and len(root) == 1:
            root, shift = root[0], shift - _BITS
        return ValueVector(root, shift, self._size - 1, self._leaf_bits)
