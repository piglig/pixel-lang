"""Typed map heap storage, identity, tracing and value boundaries."""
import unittest
from pixellang import map_keys, equality, value_boundary
from pixellang.model import PixelError
from pixellang.value_update import edit_collection
from pixellang.vm import VM
from tests.bytecode_helpers import program, op


class TypedMapStorageTests(unittest.TestCase):
    def machine(self):
        code = program([op('LOAD', 0), op('RETURN')])
        code['enums']['@0:0'] = {'Code': {'value': 'int'}}
        return VM(code)

    def test_content_lookup_and_insertion_order(self):
        vm = self.machine()
        value = vm.allocate('map<int,str>', [(3, 'three'), (1, 'one')], None)
        items = vm.heap[value.address]['items']
        self.assertEqual(items[map_keys.wrap(vm, 1, 'int')], 'one')
        self.assertEqual(vm.materialize(value), [dict(key=3, value='three'), dict(key=1, value='one')])
        other = vm.allocate('map<int,str>', [(1, 'one'), (3, 'three')], None)
        self.assertTrue(equality.equal(vm, value, other))

    def test_string_map_materializes_as_object(self):
        vm = self.machine()
        value = vm.allocate('map<str,int>', [('a', 1)], None)
        self.assertEqual(vm.materialize(value), {'a': 1})

    def test_duplicate_equal_enum_keys_and_signed_zero_are_rejected(self):
        vm = self.machine()
        first = vm.allocate('@0:0', {'$variant': 'Code', 'value': 1}, None)
        second = vm.allocate('@0:0', {'$variant': 'Code', 'value': 1}, None)
        for typ, entries in [('map<@0:0,int>', [(first, 1), (second, 2)]),
                             ('map<float64,int>', [(0.0, 1), (-0.0, 2)])]:
            with self.subTest(typ=typ), self.assertRaises(PixelError) as error:
                vm.allocate(typ, entries, None)
            self.assertEqual(error.exception.code, 'collection.duplicate_key')

    def test_key_references_survive_collection_and_are_remapped_on_copy(self):
        vm = self.machine()
        key = vm.allocate('@0:0', {'$variant': 'Code', 'value': 7}, None)
        data = vm.allocate('int[]', [1], None)
        value = vm.allocate('map<@0:0,int[]>', [(key, data)], None)
        vm.stack.append(value)
        vm.collect()
        self.assertIn(key.address, vm.heap)
        copied = value_boundary.copy_graph(vm, [value], None)[0]
        vm.stack.append(copied)
        copied_key = next(iter(vm.heap[copied.address]['items']))
        self.assertNotEqual(copied_key.value, key)
        self.assertEqual(copied_key.identity, map_keys.token(vm, key, '@0:0'))
        copied_data = vm.heap[copied.address]['items'][copied_key]
        changed, _ = edit_collection(vm, copied_data, 'append', [9], 'unit', None)
        self.assertEqual(vm.materialize(changed), [1, 9])
        self.assertEqual(vm.materialize(data), [1])
        before = vm.checkpoint()
        vm.collect()
        vm.restore_checkpoint(before)
        self.assertEqual(vm.checkpoint(), before)

    def test_allocation_rejects_incorrect_key_and_value_types(self):
        vm = self.machine()
        for entries in [[(True, 'one')], [(1, 1)]]:
            with self.assertRaises(PixelError): vm.allocate('map<int,str>', entries, None)
        with self.assertRaisesRegex(PixelError, 'does not support map keys'):
            vm.allocate('map<int[],int>', [], None)


if __name__ == '__main__': unittest.main()
