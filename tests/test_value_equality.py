"""Value equality laws independent of frontend or allocation identity."""
import unittest
from unittest.mock import patch

from pixellang import equality
from pixellang.model import PixelError
from pixellang.vm import VM
from tests.bytecode_helpers import op, program


class ValueEqualityTests(unittest.TestCase):
    def machine(self, records=None, enums=None):
        code = program([op('LOAD', 0), op('RETURN')])
        code['records'].update(records or {})
        code['enums'].update(enums or {})
        return VM(code)

    def test_arrays_are_ordered_and_storage_independent(self):
        vm = self.machine()
        a = vm.allocate('int[]', [1, 2], None)
        b = vm.allocate('int[]', bytearray([1, 2]), None)
        c = vm.allocate('int[]', [2, 1], None)
        self.assertTrue(equality.equal(vm, a, b))
        self.assertFalse(equality.equal(vm, a, c))

    def test_nested_maps_ignore_insertion_order_but_compare_every_sibling(self):
        vm = self.machine()
        def nested(values):
            return vm.allocate('map<str,map<str,int>>', {
                key: vm.allocate('map<str,int>', item, None) for key, item in values
            }, None)
        a = nested([('a', {'x': 1}), ('b', {'y': 2})])
        b = nested([('b', {'y': 2}), ('a', {'x': 1})])
        c = nested([('a', {'x': 1}), ('b', {'y': 3})])
        self.assertTrue(equality.equal(vm, a, b))
        self.assertFalse(equality.equal(vm, a, c))

    def test_recursive_values_ignore_sharing_topology(self):
        from pixellang.value_update import replace_path
        vm = self.machine({'@0:0': {'value': 'int', 'children': '@0:0[]'}})
        def node(value, children=()):
            items = vm.allocate('@0:0[]', children, None)
            return vm.allocate('@0:0', {'value': value, 'children': items}, None)
        leaf = node(1)
        shared = node(0, [leaf, leaf])
        separate = node(0, [node(1), node(1)])
        vm.stack.extend([shared, separate])
        self.assertTrue(equality.equal(vm, shared, separate))
        changed = replace_path(vm, shared,
                               [{'field': 'children'}, {'index': True}, {'field': 'value'}],
                               [1], 2, None)
        self.assertFalse(equality.equal(vm, changed, separate))
        self.assertTrue(equality.equal(vm, shared, separate))

    def test_nominal_identity_and_enum_tags_matter(self):
        vm = self.machine({'@0:0': {'value': 'int'}, '@0:1': {'value': 'int'}},
                          {'@0:2': {'Ready': {}, 'Done': {}}})
        a = vm.allocate('@0:0', {'value': 1}, None)
        b = vm.allocate('@0:1', {'value': 1}, None)
        with self.assertRaisesRegex(PixelError, 'different types'):
            equality.equal(vm, a, b)
        ready = vm.allocate('@0:2', {'$variant': 'Ready'}, None)
        done = vm.allocate('@0:2', {'$variant': 'Done'}, None)
        self.assertFalse(equality.equal(vm, ready, done))

    def test_unsupported_types_rejected_even_when_empty_or_inactive(self):
        vm = self.machine(enums={'@0:0': {'Empty': {}, 'Callback': {'call': 'fn(int)->(int)'}}})
        for value in [vm.allocate('fn(int)->(int)[]', [], None),
                      vm.allocate('@0:0', {'$variant': 'Empty'}, None)]:
            with self.assertRaisesRegex(PixelError, 'does not support value equality'):
                equality.equal(vm, value, value)

    def test_comparison_is_readonly_and_budgeted(self):
        vm = self.machine()
        a = vm.allocate('int[]', [1, 2, 3], None)
        b = vm.allocate('int[]', [1, 2, 3], None)
        before = vm.checkpoint()
        self.assertTrue(equality.equal(vm, a, b))
        self.assertEqual(vm.checkpoint(), before)
        with patch.object(equality, 'MAX_COMPARISON_ITEMS', 2):
            with self.assertRaisesRegex(PixelError, 'comparison budget exceeded'):
                equality.equal(vm, a, b)
        vm.cancelled = True
        with self.assertRaisesRegex(PixelError, 'Execution cancelled'):
            equality.equal(vm, a, b)

    def test_vm_instructions_compare_contents_and_replay_one_step(self):
        for opcode, expected in [('EQ', True), ('NE', False)]:
            with self.subTest(opcode=opcode):
                code = program([op('LOAD', 0), op('RETURN')])
                code['functions']['main']['locals'] = 2
                code['functions']['main']['instructions'] = [
                    op('LOAD', 0), op('LOAD', 1), op(opcode), op('PRINT'),
                    op('PUSH', 0), op('RETURN')]
                vm = VM(code)
                vm.call_stack[0].memory[0] = vm.allocate('int[]', [1, 2], None)
                vm.call_stack[0].memory[1] = vm.allocate('int[]', [1, 2], None)
                vm.step(snapshot=False)
                vm.step(snapshot=False)
                before = vm.checkpoint()
                steps = vm.steps
                vm.step(snapshot=False)
                self.assertIs(vm.stack[-1], expected)
                self.assertEqual(vm.steps, steps + 1)
                after = vm.checkpoint()
                vm.restore_checkpoint(before)
                vm.step(snapshot=False)
                self.assertEqual(vm.checkpoint(), after)
                self.assertEqual(vm.run(), [expected])


if __name__ == '__main__':
    unittest.main()
