"""Charge immutable vector storage once per live radix-tree node.

Each tuple slot is one heap item, including internal child pointers. References
count incoming storage edges, not user references; VM objects retain tree roots.
The table holds node objects so identity keys cannot be recycled while live.
"""


class VectorBudget:
    def __init__(self):
        self.nodes = {}

    def additional(self, vector):
        pending = [(vector._root, vector._shift)]
        seen = set()
        total = 0
        while pending:
            node, shift = pending.pop()
            key = id(node)
            if not node or key in self.nodes or key in seen:
                continue
            seen.add(key)
            total += len(node)
            if shift:
                pending.extend((child, shift - 5) for child in node)
        return total

    def retain(self, vector):
        return self._retain(vector._root, vector._shift)

    def _retain(self, node, shift):
        if not node:
            return 0
        key = id(node)
        if key in self.nodes:
            self.nodes[key][1] += 1
            return 0
        self.nodes[key] = [node, 1, shift]
        total = len(node)
        if shift:
            total += sum(self._retain(child, shift - 5) for child in node)
        return total

    def release(self, vector):
        return self._release(vector._root)

    def _release(self, node):
        if not node:
            return 0
        key = id(node)
        entry = self.nodes[key]
        entry[1] -= 1
        if entry[1]:
            return 0
        del self.nodes[key]
        total = len(node)
        if entry[2]:
            total += sum(self._release(child) for child in node)
        return total
