"""Persistent value writes isolate aliases, history and failed updates."""
import unittest
from pixellang.model import PixelError
from pixellang.vm import VM, validate_bytecode
from pixellang.value_update import replace_path
from tests.bytecode_helpers import program, op


class ValueUpdateTests(unittest.TestCase):
    def code(self):
        code = program([op('LOAD', 0), op('RETURN')])
        code['records']['@0:0'] = {'items': 'int[]', 'other': 'int[]'}
        return code

    def test_instruction_copies_ancestors_and_shares_untouched_subtrees(self):
        code = self.code()
        code['constants'] = [dict(type='int', value=n) for n in [0, 9]]
        code['functions']['main']['instructions'] = [
            op('LOAD', 0), op('PUSH', 0), op('PUSH', 1),
            op('VALUE_SET', [{'field': 'items'}, {'index': True}]), op('PRINT'),
            op('LOAD', 0), op('PRINT'), op('PUSH', 0), op('RETURN')]
        vm = VM(code)
        items = vm.allocate('int[]', [1, 2], None)
        base = vm.allocate('@0:0', {'items': items, 'other': items}, None)
        vm.call_stack[0].memory[0] = base
        before = vm.checkpoint()
        self.assertEqual(vm.run(), [{'items': [9, 2], 'other': [1, 2]},
                                    {'items': [1, 2], 'other': [1, 2]}])
        self.assertEqual(before['heap'][base.address]['items']['items'], items)
        self.assertEqual(vm.heap[items.address]['items'], [1, 2])
        vm.restore_checkpoint(before)
        self.assertEqual(vm.run(), [{'items': [9, 2], 'other': [1, 2]},
                                    {'items': [1, 2], 'other': [1, 2]}])

    def test_nested_updates_preserve_sharing_and_survive_collection(self):
        vm = VM(self.code(), max_heap_items=12)
        items = vm.allocate('int[]', [1, 2], None)
        base = vm.allocate('@0:0', {'items': items, 'other': items}, None)
        vm.call_stack[0].memory[0] = base
        vm.allocate('int[]', [0] * 5, None)  # Dead storage forces collection.
        updated = replace_path(vm, base, [{'field': 'items'}, {'index': True}], [1], 8, None)
        fields = vm.heap[updated.address]['items']
        self.assertEqual(fields['other'], items)
        self.assertNotEqual(fields['items'], items)
        self.assertEqual(vm.materialize(updated), {'items': [1, 8], 'other': [1, 2]})
        self.assertEqual(vm.materialize(base), {'items': [1, 2], 'other': [1, 2]})
        self.assertEqual(vm.stack, [])

    def test_invalid_paths_types_and_budget_never_change_old_values(self):
        for path, indices, value in [
            ([{'field': 'missing'}], [], 2),
            ([{'field': 'items'}, {'index': True}], [3], 2),
            ([{'field': 'items'}, {'index': True}], [0], 'wrong'),
        ]:
            with self.subTest(path=path, indices=indices, value=value):
                vm = VM(self.code())
                items = vm.allocate('int[]', [1], None)
                base = vm.allocate('@0:0', {'items': items, 'other': items}, None)
                with self.assertRaises(PixelError):
                    replace_path(vm, base, path, indices, value, None)
                self.assertEqual(vm.materialize(base), {'items': [1], 'other': [1]})
                self.assertEqual(vm.stack, [])
        vm = VM(self.code(), max_heap_items=3)
        items = vm.allocate('int[]', [1], None)
        base = vm.allocate('@0:0', {'items': items, 'other': items}, None)
        with self.assertRaisesRegex(PixelError, 'Heap budget'):
            replace_path(vm, base, [{'field': 'items'}, {'index': True}], [0], 2, None)
        self.assertEqual(vm.materialize(base), {'items': [1], 'other': [1]})
        self.assertEqual(vm.stack, [])

    def test_map_updates_use_typed_keys_and_preserve_original(self):
        vm = VM(self.code())
        base = vm.allocate('map<str,int>', {'a': 1}, None)
        changed = replace_path(vm, base, [{'index': True}], ['a'], 4, None)
        self.assertEqual(vm.materialize(base), {'a': 1})
        self.assertEqual(vm.materialize(changed), {'a': 4})
        inserted = replace_path(vm, base, [{'index': True}], ['b'], 5, None)
        self.assertEqual(vm.materialize(inserted), {'a': 1, 'b': 5})
        self.assertEqual(vm.materialize(base), {'a': 1})
        nested = vm.allocate('map<str,int[]>', {}, None)
        with self.assertRaises(PixelError):
            replace_path(vm, nested, [{'index': True}, {'index': True}], ['missing', 0], 5, None)

    def test_large_updates_validate_changed_entries_without_rescanning_old_data(self):
        from unittest.mock import patch
        from pixellang import map_keys

        vm = VM(self.code())
        original = vm.allocate('map<str,int>', {str(i): i for i in range(1000)}, None)
        with patch.object(vm, 'typed', wraps=vm.typed) as typed, patch.object(map_keys, 'wrap', wraps=map_keys.wrap) as wrap:
            changed = replace_path(vm, original, [{'index': True}], ['500'], 7, None)
        self.assertLess(typed.call_count, 10)
        self.assertEqual(wrap.call_count, 1)
        self.assertEqual(vm.materialize(original)['500'], 500)
        self.assertEqual(vm.materialize(changed)['500'], 7)

    def test_byte_storage_updates_preserve_full_integer_range_and_original(self):
        vm = VM(self.code())
        original = vm.allocate('int[]', [1] * 4096, None)
        changed = replace_path(vm, original, [{'index': True}], [0], 1000, None)
        self.assertEqual(vm.materialize(original), [1] * 4096)
        self.assertEqual(vm.materialize(changed), [1000] + [1] * 4095)

    def test_cancellation_does_not_publish_a_value_or_leave_temporary_roots(self):
        vm = VM(self.code())
        base = vm.allocate('int[]', [1], None)
        vm.cancelled = True
        with self.assertRaisesRegex(PixelError, 'Execution cancelled'):
            replace_path(vm, base, [{'index': True}], [0], 2, None)
        self.assertEqual(vm.stack, [])
        self.assertEqual(vm.materialize(base), [1])

    def test_malformed_instruction_paths_are_rejected_before_execution(self):
        for path in [None, [], [{}], [{'index': 1}], [{'field': ''}],
                     [{'field': 'x', 'index': True}], [{'index': True}] * 129]:
            with self.subTest(path=path):
                for instruction in ('VALUE_SET', 'VALUE_CHECK'):
                    code = self.code()
                    code['functions']['main']['instructions'].insert(0, op(instruction, path))
                    with self.assertRaisesRegex(PixelError, 'Invalid value update path'):
                        validate_bytecode(code)

    def test_address_check_fails_before_rhs_instructions(self):
        code = self.code()
        code['constants'] = [dict(type='int', value=n) for n in [5, 9]]
        code['functions']['main']['instructions'] = [
            op('LOAD', 0), op('PUSH', 0), op('VALUE_CHECK', [{'index': True}]),
            op('PUSH', 1), op('PRINT'), op('PUSH', 1), op('RETURN')]
        vm = VM(code)
        base = vm.allocate('int[]', [1], None)
        vm.call_stack[0].memory[0] = base
        with self.assertRaises(PixelError):
            vm.run()
        self.assertEqual(vm.output, [])
        self.assertEqual(vm.materialize(base), [1])


if __name__ == '__main__':
    unittest.main()
