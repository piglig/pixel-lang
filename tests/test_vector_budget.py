"""Persistent array budgets count live shared storage and preserve hard limits."""
import unittest

from pixellang.model import PixelError
from pixellang.value_vector import ValueVector
from pixellang.vector_budget import VectorBudget
from pixellang.value_update import edit_collection, replace_path
from pixellang.vm import VM
from tests.bytecode_helpers import program, op


class VectorBudgetTests(unittest.TestCase):
    def test_bulk_reference_append_survives_collection_and_checkpoint(self):
        vm = VM(program([op('LOAD', 0), op('RETURN')]), max_heap_items=10000)
        children = [vm.allocate('int[]', [i], None) for i in range(80)]
        original = vm.allocate('int[][]', children[:33], None)
        source = vm.allocate('int[][]', children[33:], None)
        vm.stack.extend([original, source])
        vm.collect()
        # Force the collection performed while publishing the extended tree.
        vm.allocations_since_gc = 10000
        collections = vm.collections
        changed, _ = edit_collection(vm, original, 'extend', [source], 'unit', None)
        vm.call_stack[0].memory[0] = changed
        self.assertGreater(vm.collections, collections)
        self.assertEqual(vm.materialize(changed), [[i] for i in range(80)])
        self.assertEqual(vm.materialize(original), [[i] for i in range(33)])
        snapshot = vm.checkpoint()
        vm.restore_checkpoint(snapshot)
        vm.collect()
        self.assertEqual(vm.materialize(changed), [[i] for i in range(80)])
        self.assertEqual(vm.materialize(source), [[i] for i in range(33, 80)])
        vm.stack.clear()
        vm.call_stack[0].memory[0] = None
        vm.collect()
        self.assertEqual(vm.heap_items, 0)
        self.assertEqual(vm.vector_budget.nodes, {})

    def test_shared_tree_nodes_are_charged_once_and_released_by_last_owner(self):
        original = ValueVector.from_sequence(range(1024))
        changed = original.set(17, -1)
        budget = VectorBudget()
        self.assertEqual(budget.additional(original), 1056)
        self.assertEqual(budget.retain(original), 1056)
        self.assertEqual(budget.retain(original), 0)
        self.assertEqual(budget.additional(changed), 64)
        self.assertEqual(budget.retain(changed), 64)
        self.assertEqual(budget.release(original), 0)
        self.assertEqual(budget.release(original), 64)
        self.assertEqual(budget.release(changed), 1056)
        self.assertEqual(budget.nodes, {})
        self.assertEqual(original[17], 17)
        self.assertEqual(changed[17], -1)

    def test_branch_growth_and_repeated_children_have_exact_cost(self):
        leaf = tuple(range(32))
        shared = ValueVector((leaf, leaf), 5, 64)
        budget = VectorBudget()
        self.assertEqual(budget.additional(shared), 34)
        self.assertEqual(budget.retain(shared), 34)
        grown = shared.extend(range(100))
        charge = budget.additional(grown)
        self.assertEqual(budget.retain(grown), charge)
        released = budget.release(shared) + budget.release(grown)
        self.assertEqual(released, 34 + charge)
        self.assertEqual(budget.nodes, {})

    def test_vm_updates_collect_dead_versions_and_restore_budget_identity(self):
        vm = VM(program([op('LOAD', 0), op('RETURN')]), max_heap_items=4000)
        original = vm.allocate('int[]', list(range(1024)), None)
        vm.stack.append(original)
        current, _ = edit_collection(vm, original, 'append', [1024], 'unit', None)
        vm.call_stack[0].memory[0] = current
        for index in range(200):
            current = replace_path(vm, current, [{'index': True}], [index], -index, None)
            vm.call_stack[0].memory[0] = current
        vm.collect()
        charge = vm.heap_items
        self.assertLess(charge, 2200)
        self.assertEqual(vm.materialize(original), list(range(1024)))
        snapshot = vm.checkpoint()
        later = replace_path(vm, current, [{'index': True}], [500], -500, None)
        vm.call_stack[0].memory[0] = later
        vm.restore_checkpoint(snapshot)
        vm.collect()
        self.assertEqual(vm.heap_items, charge)
        self.assertEqual(vm.materialize(current)[500], 500)
        self.assertEqual(vm.vector_budget.additional(vm.heap[current.address]['items']), 0)
        vm.stack.clear()
        vm.call_stack[0].memory[0] = None
        vm.collect()
        self.assertEqual(vm.heap_items, 0)
        self.assertEqual(vm.vector_budget.nodes, {})

    def test_new_storage_still_fails_at_the_budget_without_partial_publication(self):
        vm = VM(program([op('LOAD', 0), op('RETURN')]), max_heap_items=64)
        initial = vm.allocate('int[]', list(range(32)), None)
        vm.call_stack[0].memory[0] = initial
        with self.assertRaisesRegex(PixelError, 'Heap budget'):
            edit_collection(vm, initial, 'append', [32], 'unit', None)
        self.assertEqual(vm.materialize(initial), list(range(32)))
        self.assertEqual(vm.heap_items, 32)
        self.assertEqual(vm.vector_budget.nodes, {})
