"""Array storage retains old versions across tree boundaries and random edits."""
from copy import deepcopy
import random
import unittest
from pixellang.value_vector import ValueVector


class ValueVectorTests(unittest.TestCase):
    def test_bulk_append_across_leaf_and_tree_boundaries_preserves_versions(self):
        for size in (0, 1, 31, 32, 33, 1023, 1024, 1025, 32767, 32768):
            before = ValueVector.from_sequence(range(size))
            for count in (0, 1, 31, 32, 33, 1057):
                with self.subTest(size=size, count=count):
                    added = range(size, size + count)
                    after = before.extend(iter(added))
                    self.assertEqual(after, list(range(size + count)))
                    self.assertEqual(before, list(range(size)))
                    if count:
                        self.assertEqual(after.pop(), list(range(size + count - 1)))
                        self.assertEqual(after.set(size, -1)[size], -1)
                    else:
                        self.assertIs(after, before)
        before = ValueVector.from_sequence(range(1025))
        after = before.extend(before)
        self.assertEqual(after, list(range(1025)) * 2)
        self.assertIs(after._root[0], before._root[0])

    def test_bulk_append_failed_iterator_preserves_original(self):
        before = ValueVector.from_sequence(range(33))
        def failing():
            yield from range(100)
            raise ValueError('source failed')
        with self.assertRaisesRegex(ValueError, 'source failed'):
            before.extend(failing())
        self.assertEqual(before, list(range(33)))

    def test_tree_growth_shrinkage_and_bulk_construction_boundaries(self):
        for size in (0, 1, 31, 32, 33, 1023, 1024, 1025, 32768, 32769):
            with self.subTest(size=size):
                expected = list(range(size))
                built = ValueVector.from_sequence(expected)
                self.assertEqual(list(built), expected)
                appended = built.append(size)
                self.assertEqual(appended[-1], size)
                self.assertEqual(appended.pop(), built)
                self.assertEqual(list(built), expected)
                if size:
                    self.assertEqual(built[0], 0)
                    self.assertEqual(built[-1], size-1)
                    self.assertEqual(built.pop(), expected[:-1])
        value = ValueVector()
        for i in range(1100):
            value = value.append(i)
        for i in reversed(range(1100)):
            self.assertEqual(value[-1], i)
            value = value.pop()
        self.assertEqual(value, [])

    def test_random_updates_preserve_history(self):
        randomizer = random.Random(43)
        value, expected, history = ValueVector(), [], []
        for step in range(2500):
            if step % 101 == 0:
                history.append((value, expected.copy()))
            operation = randomizer.randrange(3) if expected else 0
            if operation == 0:
                value = value.append(step)
                expected.append(step)
            elif operation == 1:
                index = randomizer.randrange(len(expected))
                value = value.set(index, -step)
                expected[index] = -step
            else:
                value = value.pop()
                expected.pop()
            self.assertEqual(value, expected)
        for old, snapshot in history:
            self.assertEqual(old, snapshot)

    def test_updates_share_unaffected_branches_and_checkpoint_copies(self):
        before = ValueVector.from_sequence(list(range(32769)))
        after = before.set(0, -1)
        self.assertIs(before._root[1], after._root[1])
        self.assertIsNot(before._root[0], after._root[0])
        self.assertEqual(before[0], 0)
        self.assertEqual(after[0], -1)
        snapshot = deepcopy({'array': after})
        self.assertEqual(snapshot['array'], after)
        self.assertEqual(after[10:20:2], [10, 12, 14, 16, 18])
        self.assertEqual(ValueVector.from_sequence([1, 2]) * 2, [1, 2, 1, 2])
        self.assertEqual(after.extend(after[:2])[-2:], [-1, 1])

    def test_vm_vector_storage_supports_gc_checkpoints_and_independent_updates(self):
        from pixellang.vm import VM
        from pixellang.value_update import edit_collection
        from tests.bytecode_helpers import program, op

        vm = VM(program([op('LOAD', 0), op('RETURN')]))
        initial = vm.allocate('int[]', [1, 2], None)
        changed, _ = edit_collection(vm, initial, 'append', [3], 'unit', None)
        vm.call_stack[0].memory[0] = changed
        self.assertIsInstance(vm.heap[changed.address]['items'], ValueVector)
        snapshot = vm.checkpoint()
        further, _ = edit_collection(vm, changed, 'append', [4], 'unit', None)
        self.assertEqual(vm.materialize(changed), [1, 2, 3])
        self.assertEqual(vm.materialize(further), [1, 2, 3, 4])
        vm.collect()
        self.assertEqual(vm.materialize(changed), [1, 2, 3])
        vm.restore_checkpoint(snapshot)
        self.assertEqual(vm.materialize(changed), [1, 2, 3])

    def test_nested_reference_array_updates_survive_forced_collection(self):
        from pixellang.vm import VM
        from pixellang.value_update import replace_path
        from tests.bytecode_helpers import program, op

        vm = VM(program([op('LOAD', 0), op('RETURN')]), max_heap_items=125)
        children = [vm.allocate('int[]', [i], None) for i in range(40)]
        original = vm.allocate('int[][]', children, None)
        vm.stack.append(original)
        vm.allocate('int[]', [0] * 40, None)  # Dead allocation forces collection on update.
        changed = replace_path(vm, original, [{'index': True}, {'index': True}], [32, 0], 999, None)
        vm.stack.append(changed)
        self.assertGreater(vm.collections, 0)
        vm.collect()
        self.assertEqual(vm.materialize(original), [[i] for i in range(40)])
        expected = [[i] for i in range(40)]
        expected[32] = [999]
        self.assertEqual(vm.materialize(changed), expected)
        snapshot = vm.checkpoint()
        vm.restore_checkpoint(snapshot)
        self.assertEqual(vm.materialize(original), [[i] for i in range(40)])
        self.assertEqual(vm.materialize(changed), expected)

    def test_out_of_bounds_does_not_change_storage(self):
        value = ValueVector.from_sequence([1, 2])
        for index in (2, -3):
            with self.assertRaises(IndexError):
                value[index]
        for index in (-1, 2):
            with self.assertRaises(IndexError):
                value.set(index, 3)
        with self.assertRaises(IndexError):
            ValueVector().pop()
        self.assertEqual(value, [1, 2])
