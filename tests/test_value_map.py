"""Persistent map ordering, collision handling, isolation and bounded churn."""
from copy import deepcopy
from dataclasses import dataclass
import random
import unittest
from pixellang.value_map import ValueMap


@dataclass(frozen=True)
class CollidingKey:
    value: int
    def __hash__(self):
        return 1


class ValueMapTests(unittest.TestCase):
    def test_random_operations_preserve_insertion_order_and_old_versions(self):
        rng = random.Random(73)
        value, expected, history = ValueMap(), {}, []
        for step in range(4000):
            key = rng.randrange(600)
            if step % 193 == 0:
                history.append((value, expected.copy()))
            if rng.randrange(3):
                value = value.set(key, step)
                expected[key] = step
            else:
                value = value.remove(key)
                expected.pop(key, None)
            self.assertEqual(list(value.items()), list(expected.items()))
            self.assertEqual(len(value), len(expected))
            self.assertEqual(value.get(key), expected.get(key))
        for old, snapshot in history:
            self.assertEqual(list(old.items()), list(snapshot.items()))
        self.assertEqual(deepcopy(value), expected)

    def test_collisions_updates_deletion_and_reinsertion(self):
        keys = [CollidingKey(i) for i in range(50)]
        original = ValueMap.from_mapping(dict(zip(keys, range(50))))
        changed = original.set(keys[0], 99).remove(keys[20]).set(keys[20], 88)
        self.assertEqual(original[keys[0]], 0)
        self.assertEqual(changed[keys[0]], 99)
        self.assertEqual(changed[keys[20]], 88)
        self.assertEqual(list(changed)[-1], keys[20])
        self.assertEqual(list(original), keys)
        for key in keys:
            changed = changed.remove(key)
        self.assertEqual(dict(changed), {})

    def test_shared_prefix_hashes_and_unequal_negative_keys(self):
        keys = [-1, -2, 0, 1 << 5, 1 << 10, 1 << 40, 1 << 60]
        value = ValueMap.from_mapping({key: i for i, key in enumerate(keys)})
        for i, key in enumerate(keys):
            self.assertEqual(value[key], i)
        for key in keys:
            value = value.remove(key)
        self.assertEqual(len(value), 0)
        with self.assertRaises(KeyError):
            value[0]

    def test_churn_compacts_order_slots_without_changing_retained_versions(self):
        value = ValueMap.from_mapping({i: i for i in range(100)})
        before = value
        for i in range(2000):
            value = value.remove(i).set(i+100, i+100)
        self.assertEqual(list(value), list(range(2000, 2100)))
        self.assertLessEqual(len(value._order), 2*len(value))
        self.assertEqual(list(before), list(range(100)))
        self.assertIs(value.remove(-1), value)

    def test_vm_nested_values_equality_gc_debug_and_independent_updates(self):
        from pixellang.vm import VM
        from pixellang.value_update import replace_path
        from pixellang.debugvalues import children
        from pixellang.equality import equal
        from tests.bytecode_helpers import program, op

        vm = VM(program([op('LOAD', 0), op('RETURN')]))
        child = vm.allocate('int[]', [1], None)
        original = vm.allocate('map<str,int[]>', {'a': child}, None)
        vm.stack.append(original)
        changed = replace_path(vm, original, [{'index': True}, {'index': True}], ['a', 0], 2, None)
        vm.stack.append(changed)
        matching = vm.allocate('map<str,int[]>', {'a': vm.allocate('int[]', [2], None)}, None)
        vm.stack.append(matching)
        self.assertTrue(equal(vm, changed, matching, None))
        self.assertTrue(equal(vm, matching, changed, None))
        self.assertFalse(equal(vm, changed, original, None))
        further = replace_path(vm, changed, [{'index': True}, {'index': True}], ['a', 0], 3, None)
        self.assertIsInstance(vm.heap[further.address]['items'], ValueMap)
        self.assertEqual(vm.materialize(further), {'a': [3]})
        self.assertEqual(vm.materialize(changed), {'a': [2]})
        view, named, size = children(vm, changed)
        self.assertEqual((named, size), (True, 1))
        self.assertEqual([name for name, _ in view], ['a'])
        vm.collect()
        self.assertEqual(vm.materialize(original), {'a': [1]})
        self.assertEqual(vm.materialize(changed), {'a': [2]})
        snapshot = vm.checkpoint()
        vm.restore_checkpoint(snapshot)
        self.assertEqual(vm.materialize(changed), {'a': [2]})

    def test_reference_keys_remain_gc_roots_after_persistent_update(self):
        from pixellang.vm import VM
        from pixellang.value_update import replace_path
        from tests.bytecode_helpers import program, op

        code = program([op('LOAD', 0), op('RETURN')])
        code['enums']['@0:1'] = {'Key': {'value': 'int'}}
        vm = VM(code)
        key = vm.make_enum('@0:1', 'Key', ['value'], [7], None)
        original = vm.allocate('map<@0:1,int>', [(key, 1)], None)
        vm.stack.append(original)
        changed = replace_path(vm, original, [{'index': True}], [key], 2, None)
        vm.stack[:] = [changed]
        vm.collect()
        self.assertIn(key.address, vm.heap)
        self.assertEqual(vm.materialize(key), {'variant': 'Key', 'fields': {'value': 7}})
        self.assertEqual(list(vm.heap[changed.address]['items'].values()), [2])
