"""Remaining flow graph copies preserve identity and bounded atomic publication."""
import unittest

from pixellang import value_boundary as boundary
from pixellang.model import PixelError
from pixellang.vm import VM
from tests.bytecode_helpers import op, program


class ValueGraphBoundaryTests(unittest.TestCase):
    def graph(self, width=2):
        vm = VM(program([op('LOAD', 0), op('RETURN')]))
        child = vm.allocate('int[]', [1], None)
        root = vm.allocate('int[][]', [child] * width, None)
        vm.stack.append(root)
        return vm, root, child

    def test_copy_preserves_shared_children_and_source_types(self):
        vm, root, child = self.graph()
        copied, = boundary.copy_graph(vm, [root], None)
        vm.stack.append(copied)
        first, second = vm.heap[copied.address]['items']
        self.assertEqual(first, second)
        self.assertNotEqual(first, child)
        self.assertEqual(vm.value_type(copied), 'int[][]')
        self.assertEqual(vm.materialize(copied), [[1], [1]])
        vm.collect()
        self.assertEqual(vm.materialize(root), vm.materialize(copied))
        snapshot = vm.checkpoint()
        vm.restore_checkpoint(snapshot)
        self.assertEqual(vm.materialize(copied), [[1], [1]])

    def test_budget_and_cancellation_do_not_publish_partial_copies(self):
        vm, root, _ = self.graph()
        before = vm.checkpoint()
        for options in ({'max_work': 1}, {'cancelled': lambda: True}):
            with self.subTest(options=options), self.assertRaises(PixelError):
                boundary.copy_graph(vm, [root], None, **options)
            self.assertEqual(vm.heap, before['heap'])
            self.assertEqual(vm.next_address, before['next_address'])
        for limit in (0, -1, True, 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                boundary.copy_graph(vm, [root], None, max_work=limit)
        vm.max_heap_items = vm.heap_items
        with self.assertRaisesRegex(PixelError, 'Heap budget exceeded'):
            boundary.copy_graph(vm, [root], None)
        self.assertEqual(vm.heap, before['heap'])

    def test_partial_heap_publication_rolls_back_addresses_and_counters(self):
        vm, root, _ = self.graph()
        before = vm.checkpoint()

        class InterruptedHeap(dict):
            def update(self, entries):
                address, value = next(iter(entries.items()))
                self[address] = value
                raise MemoryError('injected partial publication')

        vm.heap = InterruptedHeap(vm.heap)
        with self.assertRaisesRegex(MemoryError, 'partial publication'):
            boundary.copy_graph(vm, [root], None)
        self.assertEqual(vm.heap, before['heap'])
        for counter in ('next_address', 'heap_items', 'allocations_since_gc'):
            self.assertEqual(getattr(vm, counter), before[counter])

    def test_wide_graph_edges_are_visited_lazily(self):
        vm, root, _ = self.graph(10000)
        visited = []

        class ObservedEdges(list):
            def __iter__(self):
                for value in super().__iter__():
                    visited.append(value)
                    yield value

        vm.heap[root.address]['items'] = ObservedEdges(vm.heap[root.address]['items'])
        before = (vm.next_address, vm.heap_items)
        with self.assertRaisesRegex(PixelError, 'work budget exceeded'):
            boundary.copy_graph(vm, [root], None, max_work=5)
        self.assertLessEqual(len(visited), 4)
        self.assertEqual((vm.next_address, vm.heap_items), before)
        visited.clear()
        with self.assertRaisesRegex(PixelError, 'Execution cancelled'):
            boundary.copy_graph(vm, [root], None, cancelled=lambda: len(visited) >= 2)
        self.assertEqual(len(visited), 2)
        self.assertEqual((vm.next_address, vm.heap_items), before)
