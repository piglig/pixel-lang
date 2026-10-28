"""Typed map values remain data-only across spatial and durable snapshots."""
import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from pixellang import equality, map_keys, jsonvalue
from pixellang.model import PixelError
from pixellang.native_state import NativeStateCodec
from pixellang.event_journal import JournaledApplicationRuntime
from pixellang.native_events import NativeApplicationRuntime
from tests.test_native_events_runtime import application_program
from tests.bytecode_helpers import op
from pixellang.spatial_values import FrozenValue, CoordinateFrame, SpatialValue
from pixellang.value_update import edit_collection
from pixellang.vm import VM
from tests.test_native_spatial_vm import program


class TypedMapStateTests(unittest.TestCase):
    def machine(self):
        code = program()
        code['enums']['@0:1'] = {'Code': {'value': 'int'}}
        return VM(code)

    def test_enum_keys_nested_maps_freeze_persist_thaw_and_detach(self):
        vm = self.machine()
        key = vm.allocate('@0:1', {'$variant': 'Code', 'value': 7}, None)
        array = vm.allocate('int[]', [1, 2], None)
        nested = vm.allocate('map<str,int[]>', [('values', array)], None)
        original = vm.allocate('map<@0:1,map<str,int[]>>', [(key, nested)], None)
        vm.stack.append(original)
        frozen = vm.freeze_spatial_payload(original, None)
        document = NativeStateCodec(vm).encode(frozen)
        restored = NativeStateCodec(vm).decode(json.loads(json.dumps(document, sort_keys=True)))
        self.assertEqual(frozen, restored)
        self.assertEqual(vm.materialize(original), vm.materialize(restored))
        self.assertEqual(map_keys.token(vm, key, '@0:1'),
                         map_keys.token(vm, restored.items[0][0], '@0:1', frozen=True))
        # Force collection during every thaw allocation, including between key and value.
        allocate = vm.allocate
        def force_gc(*args, **kwargs):
            vm.allocations_since_gc = 10000
            return allocate(*args, **kwargs)
        vm.allocate = force_gc
        thawed = vm.thaw_spatial_payload(restored, None)
        self.assertEqual(vm.value_type(thawed), vm.value_type(original))
        self.assertEqual(vm.materialize(thawed), vm.materialize(original))
        vm.stack.append(thawed)
        nested_copy = next(iter(vm.heap[thawed.address]['items'].values()))
        array_copy = next(iter(vm.heap[nested_copy.address]['items'].values()))
        changed, _ = edit_collection(vm, array_copy, 'append', [3], 'unit', None)
        self.assertEqual(vm.materialize(changed), [1, 2, 3])
        self.assertEqual(vm.materialize(array), [1, 2])
        self.assertEqual(vm.materialize(restored)[0]['value']['values'], [1, 2])

    def test_journal_recovers_typed_map_state_output_and_pending_input(self):
        code = application_program()
        typ = 'map<int,str>'
        code['enums']['@0:3']['Set']['value'] = typ
        code['constants'].append(dict(type='str', value='initial'))
        graph = code['flows']['delay']
        graph.update(stateful=True,
                     states=[dict(node=7, name='table', slot=2, type=typ, initializer='initial')],
                     updates=[dict(node=8, state=7, expression=9)],
                     dependencies=[dict(source=7,target=8,reference=9),dict(source=7,target=6,reference=5)],
                     output=dict(node=5, slot=3, type=typ))
        code['functions']['initial'] = dict(params=[], locals=0, result=typ, instructions=[
            op('PUSH', 0), op('PUSH', 1), op('MAP', dict(type=typ, count=2)), op('RETURN')])
        code['functions']['delay'].update(locals=4, result=typ, instructions=[
            dict(op=name, arg=arg, node=node, module=0) for name, arg, node in [
                ('FLOW_READ', 7, 7), ('STORE', 2, 7), ('LOAD', 2, 9),
                ('FLOW_STAGE', 7, 8), ('LOAD', 2, 5), ('STORE', 3, 5), ('LOAD', 3, 5), ('RETURN', None, 6)]])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'typed-map.pxjournal'
            runtime = JournaledApplicationRuntime(VM(code), path, checkpoint_interval=2)
            self.addCleanup(runtime.close)
            event = {'variant': 'Set', 'fields': {'value': [{'key': 9, 'value': 'queued'}]}}
            runtime.enqueue(event, tick=10)
            for _ in range(3):
                self.assertEqual(runtime.step(), [{'key': 0, 'value': 'initial'}])
            state = runtime.vm.flow_runtime.instances[0].cells
            runtime.close()
            original_step = NativeApplicationRuntime.step
            with patch.object(NativeApplicationRuntime, 'step', autospec=True, side_effect=original_step) as step:
                recovered = JournaledApplicationRuntime(VM(code), path, checkpoint_interval=2)
                self.addCleanup(recovered.close)
                self.assertEqual(step.call_count, 1)
            self.assertEqual(recovered.vm.flow_runtime.instances[0].cells, state)
            self.assertEqual(recovered.view(), [{'key': 0, 'value': 'initial'}])
            self.assertEqual(recovered.input_value(recovered.pending[0][2]), event)
            self.assertEqual(recovered.step(), [{'key': 0, 'value': 'initial'}])

    def test_array_of_maps_retains_outer_array_semantics(self):
        vm = self.machine()
        table = vm.allocate('map<int,str>', [(1, 'one')], None)
        values = vm.allocate('map<int,str>[]', [table], None)
        vm.stack.append(values)
        expected = [[{'key': 1, 'value': 'one'}]]
        self.assertEqual(vm.materialize(values), expected)
        encoded = jsonvalue.stringify(vm.to_json(values, None))
        self.assertEqual(json.loads(encoded), expected)
        decoded = vm.decode_json(encoded, 'map<int,str>[]', None)
        self.assertTrue(equality.equal(vm, values, decoded))
        frozen = vm.freeze_spatial_payload(values, None)
        self.assertEqual(vm.materialize(frozen), expected)
        restored = NativeStateCodec(vm).decode(NativeStateCodec(vm).encode(frozen))
        self.assertTrue(equality.equal(vm, values, vm.thaw_spatial_payload(restored, None)))
        json.dumps(vm.state())
        vm.stack[:] = [frozen]
        vm.event_waiting = True
        vm.recount_spatial()
        self.assertEqual(vm.spatial_items, 4)

    def test_decoder_rejects_duplicate_keys_bad_types_and_malformed_entries(self):
        vm = self.machine()
        key = FrozenValue('@0:1', (('$variant', 'Code'), ('value', 7)))
        cases = [
            FrozenValue('map<float64,int>', ((0.0, 1), (-0.0, 2))),
            FrozenValue('map<@0:1,int>', ((key, 1), (key, 2))),
            FrozenValue('map<int,str>', ((True, 'one'),)),
            FrozenValue('map<int,str>', ((1, 2),)),
            FrozenValue('map<int,str>', ((1, 'one', 'extra'),)),
            FrozenValue('map<int[],str>', ()),
        ]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(PixelError):
                NativeStateCodec(vm).decode(NativeStateCodec(vm).encode(value))

    def test_spatial_keys_count_towards_live_budget_in_mutable_and_frozen_maps(self):
        vm = self.machine()
        point = SpatialValue('Point3', CoordinateFrame('@0:0', 'meters'), (1.0, 2.0, 3.0))
        value = vm.allocate('map<Point3<@0:0>,int>', [(point, 1)], None)
        vm.stack.append(value)
        vm.event_waiting = True
        vm.recount_spatial()
        self.assertEqual(vm.spatial_items, 12)
        frozen = vm.freeze_spatial_payload(value, None)
        vm.stack[:] = [frozen]
        vm.recount_spatial()
        self.assertEqual(vm.spatial_items, 14)
        restored = NativeStateCodec(vm).decode(NativeStateCodec(vm).encode(frozen))
        self.assertEqual(restored, frozen)
        thawed = vm.thaw_spatial_payload(restored, None)
        self.assertTrue(vm.builtin('has', [thawed, point], None))


if __name__ == '__main__':
    unittest.main()
