"""Functional collection instructions used by writable value receivers."""
import unittest
from pixellang.model import PixelError
from pixellang.vm import VM, validate_bytecode
from pixellang.value_update import edit_collection
from tests.bytecode_helpers import program, op


class ValueCollectionTests(unittest.TestCase):
    def code(self):
        code = program([op('LOAD', 0), op('RETURN')])
        code['enums']['Option<int>'] = {'Some': {'value': 'int'}, 'None': {}}
        return code

    def test_append_extend_and_pop_preserve_old_values(self):
        vm = VM(self.code())
        original = vm.allocate('int[]', [1], None)
        appended, unit = edit_collection(vm, original, 'append', [2], 'unit', None)
        extended, _ = edit_collection(vm, appended, 'extend', [original], 'unit', None)
        popped, result = edit_collection(vm, extended, 'pop', [], 'Option<int>', None)
        self.assertEqual(unit, 0)
        self.assertEqual(vm.materialize(original), [1])
        self.assertEqual(vm.materialize(appended), [1, 2])
        self.assertEqual(vm.materialize(extended), [1, 2, 1])
        self.assertEqual(vm.materialize(popped), [1, 2])
        self.assertEqual(vm.materialize(result), {'variant': 'Some', 'fields': {'value': 1}})
        self.assertEqual(vm.stack, [])

    def test_empty_pop_and_missing_removal_return_none(self):
        vm = VM(self.code())
        empty = vm.allocate('int[]', [], None)
        updated, result = edit_collection(vm, empty, 'pop', [], 'Option<int>', None)
        self.assertEqual(vm.materialize(updated), [])
        self.assertEqual(vm.materialize(result), {'variant': 'None', 'fields': {}})
        original = vm.allocate('map<str,int>', {'a': 1}, None)
        removed, result = edit_collection(vm, original, 'remove', ['a'], 'Option<int>', None)
        self.assertEqual(vm.materialize(result), {'variant': 'Some', 'fields': {'value': 1}})
        self.assertEqual(vm.materialize(removed), {})
        missing, result = edit_collection(vm, original, 'remove', ['b'], 'Option<int>', None)
        self.assertEqual(vm.materialize(missing), {'a': 1})
        self.assertEqual(vm.materialize(original), {'a': 1})
        self.assertEqual(vm.materialize(result), {'variant': 'None', 'fields': {}})

    def test_instruction_returns_updated_receiver_and_result(self):
        code = self.code()
        code['constants'] = [dict(type='int', value=2)]
        code['functions']['main']['instructions'] = [
            op('LOAD', 0), op('PUSH', 0), op('VALUE_COLLECTION', {'method': 'append', 'result': 'unit'}),
            op('POP'), op('PRINT'), op('LOAD', 0), op('PRINT'), op('PUSH', 0), op('RETURN')]
        vm = VM(code)
        vm.call_stack[0].memory[0] = vm.allocate('int[]', [1], None)
        self.assertEqual(vm.run(), [[1, 2], [1]])

    def test_budget_failure_during_optional_result_does_not_publish_mutation(self):
        vm = VM(self.code(), max_heap_objects=2)
        original = vm.allocate('int[]', [1], None)
        with self.assertRaisesRegex(PixelError, 'Heap budget'):
            edit_collection(vm, original, 'pop', [], 'Option<int>', None)
        self.assertEqual(vm.materialize(original), [1])
        self.assertEqual(vm.stack, [])

    def test_empty_extension_needs_no_extra_collection_allocation(self):
        vm = VM(self.code(), max_heap_objects=2)
        original = vm.allocate('int[]', [1], None)
        empty = vm.allocate('int[]', [], None)
        updated, unit = edit_collection(vm, original, 'extend', [empty], 'unit', None)
        self.assertEqual(vm.materialize(updated), [1])
        self.assertEqual(unit, 0)
        self.assertEqual(len(vm.heap), 2)

    def test_wrong_types_and_result_contracts_are_rejected(self):
        vm = VM(self.code())
        original = vm.allocate('int[]', [1], None)
        for method, args, result in [('append', ['bad'], 'unit'), ('pop', [], 'Option<str>'),
                                     ('remove', [0], 'Option<int>')]:
            with self.subTest(method=method), self.assertRaises(PixelError):
                edit_collection(vm, original, method, args, result, None)
        self.assertEqual(vm.materialize(original), [1])
        for arg in [None, {}, {'method': 'unknown', 'result': 'unit'},
                    {'method': 'append', 'result': 'int'}, {'method': 'pop', 'result': 'int'}]:
            with self.subTest(arg=arg), self.assertRaises(PixelError):
                code = self.code()
                code['functions']['main']['instructions'].insert(0, op('VALUE_COLLECTION', arg))
                validate_bytecode(code)


if __name__ == '__main__':
    unittest.main()
