"""Packed integer leaves preserve values, sharing and accounting across boundaries."""
from copy import deepcopy
import random
import unittest

from pixellang.value_vector import ValueVector
from pixellang.vector_budget import VectorBudget
from pixellang.recording import footprint


class PackedValueVectorTests(unittest.TestCase):
    def test_boundaries_promotion_and_root_shrinking_preserve_snapshots(self):
        for size in (0, 1, 255, 256, 257, 8191, 8192, 8193, 262144):
            with self.subTest(size=size):
                original = ValueVector.from_sequence([7] * size, packed=True)
                grown = original.extend([255, -1, -(1 << 63), (1 << 63) - 1])
                self.assertEqual(list(grown)[size:], [255, -1, -(1 << 63), (1 << 63) - 1])
                self.assertEqual(original, [7] * size)
                self.assertEqual(deepcopy(grown), grown)
                changed = grown.set(size, 999)
                self.assertEqual(grown[size], 255)
                self.assertEqual(changed[size], 999)
                for _ in range(4):
                    grown = grown.pop()
                self.assertEqual(grown, original)
                self.assertEqual(grown._shift, original._shift)

    def test_random_edits_and_self_extension_match_plain_list(self):
        rng = random.Random(551)
        value = ValueVector.from_sequence([], packed=True)
        expected = []
        history = []
        for iteration in range(2500):
            if iteration % 97 == 0:
                history.append((value, list(expected)))
            op = rng.randrange(4)
            number = rng.choice([0, 255, 256, -1, -(1 << 63), (1 << 63) - 1])
            if op == 0 or not expected:
                value = value.append(number); expected.append(number)
            elif op == 1:
                index = rng.randrange(len(expected))
                value = value.set(index, number); expected[index] = number
            elif op == 2:
                value = value.pop(); expected.pop()
            else:
                value = value.extend([number] * 3); expected.extend([number] * 3)
            self.assertEqual(value, expected)
        self.assertEqual(value.extend(value), expected * 2)
        for snapshot, items in history:
            self.assertEqual(snapshot, items)

    def test_compact_storage_keeps_logical_budget_and_shared_nodes(self):
        value = ValueVector.from_sequence([7] * 100000, packed=True)
        changed = value.set(99999, -1)
        self.assertIs(changed._root[0], value._root[0])
        self.assertLess(footprint(value), 160000)
        budget = VectorBudget()
        first = budget.additional(value)
        self.assertGreaterEqual(first, len(value))
        self.assertEqual(budget.retain(value), first)
        delta = budget.additional(changed)
        self.assertLess(delta, 400)
        self.assertEqual(budget.retain(changed), delta)
        released = budget.release(value) + budget.release(changed)
        self.assertEqual(released, first + delta)
        self.assertEqual(budget.nodes, {})
