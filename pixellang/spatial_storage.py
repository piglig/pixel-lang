"""Storage accounting for persistent spatial tables.

Count new map/vector nodes and slots, pruning shared branches by identity. Counts
are structural accounting units, not Python allocator bytes.
"""
from .value_map import _Branch, _Leaf


def _hash_cost(node, previous):
    if node is previous or node is None:
        return 0
    if isinstance(node, _Leaf):
        return 1 + 2 * len(node.entries)
    cost = 1 + len(node.children)
    old = {}
    if isinstance(previous, _Branch):
        bits = previous.bitmap
        for child in previous.children:
            bit = bits & -bits
            old[bit] = child
            bits ^= bit
    bits = node.bitmap
    for child in node.children:
        bit = bits & -bits
        cost += _hash_cost(child, old.get(bit))
        bits ^= bit
    return cost


def _vector_cost(node, shift, previous, old_shift):
    if node is previous:
        return 0
    cost = 1 + len(node)
    if shift == 0:
        # Map order leaves contain immutable (key, value) pairs or tombstones.
        # Unchanged pairs remain shared even when their containing leaf is copied.
        for index, pair in enumerate(node):
            old_pair = previous[index] if previous is not None and old_shift == 0 and index < len(previous) else None
            if pair is not None and pair is not old_pair:
                cost += 1 + len(pair)
        return cost
    if shift > old_shift:
        return cost + sum(_vector_cost(child, shift - 5,
            previous if index == 0 else None, old_shift) for index, child in enumerate(node))
    return cost + sum(_vector_cost(child, shift - 5,
        previous[index] if previous is not None and index < len(previous) else None,
        old_shift - 5) for index, child in enumerate(node))


def allocation_cost(current, previous=None):
    if current is previous:
        return 0
    old_vector = previous._order if previous is not None else None
    return (2 + _hash_cost(current._root, previous._root if previous is not None else None)
            + _vector_cost(current._order._root, current._order._shift,
                           old_vector._root if old_vector is not None else None,
                           old_vector._shift if old_vector is not None else 0))


def storage_children(value):
    """Return (slot cost, children) for shared storage traversal at idle boundaries."""
    from .value_map import ValueMap
    from .value_vector import ValueVector
    if type(value) is ValueMap:
        return 1, (value._root, value._order)
    if type(value) is ValueVector:
        return 1, (value._root,)
    if type(value) is _Branch:
        return 1 + len(value.children), value.children
    if type(value) is _Leaf:
        return 1 + 2 * len(value.entries), ()
    if type(value) is tuple:
        return 1 + len(value), value
    return None
