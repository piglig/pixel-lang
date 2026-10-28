"""Typed map VM operations, including debug snapshots and GC boundaries."""
import json
import unittest

from pixellang import equality, jsonvalue
from pixellang.model import PixelError
from pixellang.value_update import edit_collection
from pixellang.vm import VM
from tests.bytecode_helpers import program, op


class TypedMapOperationTests(unittest.TestCase):
    def machine(self, instructions):
        code = program([op('LOAD', 0), op('RETURN')])
        code['functions']['main']['instructions'] = instructions
        code['enums']['@0:0'] = {'Code': {'value': 'int'}}
        code['enums']['Option<str>'] = {'Some': {'value': 'str'}, 'None': {}}
        return VM(code, trace=True)

    def test_map_index_and_checkpoint_replay(self):
        vm = self.machine([op('MAP', {'type': 'map<int,str>', 'count': 4}),
                           op('STORE', 0), op('LOAD', 0), op('LOAD', 0)])
        vm.stack[:] = [3, 'three', 1, 'one']
        vm.step()
        value = vm.stack[-1]
        self.assertEqual(vm.steps, 1)
        self.assertEqual(vm.trace[-1]['op'], 'MAP')
        state = json.loads(json.dumps(vm.state()))
        self.assertEqual(state['heap'][str(value.address)]['items'],
                         [{'key': 3, 'value': 'three'}, {'key': 1, 'value': 'one'}])
        vm.step()
        before = vm.checkpoint()
        vm.step()
        after = vm.checkpoint()
        vm.restore_checkpoint(before)
        vm.step()
        self.assertEqual(vm.checkpoint(), after)

    def test_value_update_lookup_remove_and_order(self):
        vm = self.machine([op('VALUE_SET', [{'index': True}]), op('INDEX'), op('VALUE_SET', [{'index': True}])])
        value = vm.allocate('map<int,str>', [(3, 'three'), (1, 'one')], None)
        vm.call_stack[0].memory[0] = value
        original = value
        vm.stack[:] = [value, 3, 'THREE']
        vm.step()
        value = vm.stack.pop()
        self.assertEqual(vm.materialize(original), [{'key': 3, 'value': 'three'}, {'key': 1, 'value': 'one'}])
        vm.stack[:] = [value, 3]
        vm.step()
        self.assertEqual(vm.stack.pop(), 'THREE')
        self.assertTrue(vm.builtin('has', [value, 1], None))
        self.assertEqual(vm.materialize(vm.builtin('lookup', [value, 3], None)),
                         {'variant': 'Some', 'fields': {'value': 'THREE'}})
        self.assertEqual(vm.materialize(vm.builtin('lookup', [value, 9], None)),
                         {'variant': 'None', 'fields': {}})
        self.assertEqual(vm.materialize(vm.builtin('keys', [value], None)), [3, 1])
        value, removed = edit_collection(vm, value, 'remove', [3], 'Option<str>', None)
        self.assertEqual(vm.materialize(removed), {'variant': 'Some', 'fields': {'value': 'THREE'}})
        value, removed = edit_collection(vm, value, 'remove', [3], 'Option<str>', None)
        self.assertEqual(vm.materialize(removed), {'variant': 'None', 'fields': {}})
        vm.stack[:] = [value, 3, 'again']
        vm.step()
        value = vm.stack.pop()
        self.assertEqual(vm.materialize(vm.builtin('keys', [value], None)), [1, 3])

    def test_json_roundtrip_preserves_typed_keys_and_nested_values(self):
        vm = self.machine([op('LOAD', 0)])
        key = vm.allocate('@0:0', {'$variant': 'Code', 'value': 7}, None)
        nested = vm.allocate('map<int,str>', [(2, 'two'), (1, 'one')], None)
        vm.stack.extend([key, nested])
        for typ, entries in [('map<str,int>', [('a', 2)]),
                             ('map<int,str>', [(2, 'two'), (1, 'one')]),
                             ('map<@0:0,map<int,str>>', [(key, nested)])]:
            with self.subTest(typ=typ):
                value = vm.allocate(typ, entries, None)
                vm.stack.append(value)
                encoded = jsonvalue.stringify(vm.to_json(value, None))
                restored = vm.decode_json(encoded, typ, None)
                self.assertTrue(equality.equal(vm, value, restored))
                self.assertEqual(vm.materialize(value), vm.materialize(restored))
                self.assertEqual(json.loads(encoded), vm.materialize(value))

    def test_json_rejects_duplicate_keys_and_bad_entry_shapes(self):
        vm = self.machine([op('LOAD', 0)])
        for source, typ, code, path in [
            ('[{"key":0.0,"value":1},{"key":-0.0,"value":2}]',
             'map<float64,int>', 'collection.duplicate_key', '$'),
            ('[{"key":1}]', 'map<int,str>', 'json.type_mismatch', '$[0]'),
            ('[{"key":"1","value":"one"}]', 'map<int,str>', 'json.type_mismatch', '$[0].key'),
        ]:
            with self.subTest(source=source), self.assertRaises(PixelError) as error:
                vm.decode_json(source, typ, None)
            self.assertEqual(error.exception.code, code)
            self.assertEqual(error.exception.path, path)
            self.assertEqual(vm.stack, [])

    def test_missing_and_incorrect_keys_fail(self):
        for key, expected in [(9, 'collection.missing_key'), (True, None)]:
            vm = self.machine([op('INDEX')])
            value = vm.allocate('map<int,str>', [(1, 'one')], None)
            vm.stack[:] = [value, key]
            with self.subTest(key=key), self.assertRaises(PixelError) as error:
                vm.step()
            if expected:
                self.assertEqual(error.exception.code, expected)
            for name in ('has', 'lookup'):
                with self.assertRaises(PixelError):
                    vm.builtin(name, [value, '1'], None)

    def test_enum_iteration_snapshot_roots_keys_and_values_during_gc(self):
        for mode in ('pair', 'single'):
            vm = self.machine([op('ITER_SNAPSHOT', mode)])
            key = vm.allocate('@0:0', {'$variant': 'Code', 'value': 7}, None)
            data = vm.allocate('int[]', [4], None)
            value = vm.allocate('map<@0:0,int[]>', [(key, data)], None)
            vm.stack[:] = [value]
            original_collect = vm.collect
            collections = []
            original_allocate = vm.allocate

            def allocate(*args, **kwargs):
                vm.allocations_since_gc = 10000
                return original_allocate(*args, **kwargs)

            def collect(*args, **kwargs):
                collections.append(True)
                return original_collect(*args, **kwargs)

            vm.allocate, vm.collect = allocate, collect
            before = vm.checkpoint()
            vm.step()
            after = vm.checkpoint()
            self.assertEqual(len(collections), 2 if mode == 'pair' else 1)
            self.assertEqual(vm.materialize(vm.stack[0]), [{'variant': 'Code', 'fields': {'value': 7}}])
            if mode == 'pair':
                self.assertEqual(vm.materialize(vm.stack[1]), [[4]])
            self.assertEqual(vm.trace[-1]['op'], 'ITER_SNAPSHOT')
            json.dumps(vm.state())
            vm.restore_checkpoint(before)
            vm.step()
            self.assertEqual(vm.checkpoint(), after)


if __name__ == '__main__':
    unittest.main()
