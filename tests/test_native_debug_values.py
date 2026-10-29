"""Native values and flow scopes use the VM at the selected time without mutation."""
from types import SimpleNamespace
import unittest
import json

from pixellang import spatial_values as spatial
from pixellang.compiler_client import CompiledArtifact
from pixellang.debugvalues import inspect_values
from pixellang.flow_debug import flow_scopes, flow_values
from pixellang.model import PixelError
from pixellang.temporal import Timeline
from pixellang.vm import VM
from pixellang.workstation import Workstation
from tests.test_native_flow_debug import program as pipeline
from tests.test_native_flow_runtime import delay_program
from tests.test_native_spatial_vm import program as spatial_program


def timeline_for(code):
    source = dict(pixels=[], metadata=dict(filename='main.pxl'))
    debug = dict(types={'@0:0': 'main.pxl:World'}, functions={
        name: dict(name=name, source='main.pxl', module='main', names=[], typeArguments=[])
        for name in code['functions']})
    return Timeline(source, compiled=CompiledArtifact(code, source, debug))


def variables(timeline, path, **options):
    return inspect_values(timeline, dict(z=timeline.cursor, path=path, **options))['variables']


class NativeDebugValueTests(unittest.TestCase):
    def test_persistent_array_paging_nested_paths_and_selected_time(self):
        from pixellang.value_update import edit_collection, replace_path
        from tests.bytecode_helpers import program, op

        timeline = timeline_for(program([op('LOAD', 0), op('RETURN')]))
        vm = timeline.vm
        original = vm.allocate('int[]', list(range(40)), None)
        changed, _ = edit_collection(vm, original, 'append', [40], 'unit', None)
        nested = vm.allocate('int[][]', [changed], None)
        vm.stack.append(nested)
        old = vm.checkpoint()
        item = variables(timeline, ['stack', 0])[0]
        self.assertEqual(item['indexedVariables'], 41)
        page = variables(timeline, item['path'], start=31, count=3)
        self.assertEqual([v['value'] for v in page], ['31', '32', '33'])
        self.assertEqual(vm.checkpoint(), old)
        newer = replace_path(vm, changed, [{'index': True}], [32], 99, None)
        vm.stack[0] = vm.allocate('int[][]', [newer], None)
        current = vm.checkpoint()
        timeline.events.append('main', 0, 1)
        timeline.replay_vm = VM(vm.bytecode)
        timeline.replay_vm.restore_checkpoint(old)
        self.assertEqual(variables(timeline, item['path'], start=32, count=1)[0]['value'], '32')
        self.assertEqual(vm.checkpoint(), current)
        self.assertEqual(timeline.replay_vm.checkpoint(), old)

    def test_native_spatial_and_frozen_values_expand_without_vm_allocations(self):
        code = spatial_program()
        vm = VM(code)
        coordinate_frame = spatial.CoordinateFrame('@0:0', 'meters')
        point = spatial.SpatialValue('Point3', coordinate_frame, (1.0, 2.0, 3.0))
        payload = spatial.FrozenValue('int[]', (10, 20, 30))
        world = spatial.SpaceValue('world', coordinate_frame, 'int[]', 'str').insert("a", point, payload)
        transform = spatial.Transform(coordinate_frame, coordinate_frame,
                                      ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)), (1.0, 2.0, 3.0))
        vm.stack.extend([point, world, world.entities[0], transform, payload])
        timeline = SimpleNamespace(cursor=0, events=[], vm=vm, compiled=SimpleNamespace(debug={'types': {'@0:0': 'main.pxl:World'}}), source=None, source_project={'entry': 'main.pxl'})
        before = vm.checkpoint()
        items = variables(timeline, ['stack'])
        self.assertEqual(items[0]['type'], 'Point3[World]')
        self.assertEqual([v['value'] for v in variables(timeline, items[0]['path'])][:3], ['1.0', '2.0', '3.0'])
        fields = {v['name']: v for v in variables(timeline, items[1]['path'])}
        entities = variables(timeline, fields['entities']['path'])
        entity = {v['name']: v for v in variables(timeline, entities[0]['path'])}
        numbers = variables(timeline, entity['payload']['path'], start=1, count=1)
        self.assertEqual([(v['name'], v['value']) for v in numbers], [('1', '20')])
        matrix = {v['name']: v for v in variables(timeline, items[3]['path'])}
        row = variables(timeline, matrix['rotation']['path'])[1]
        self.assertEqual([v['value'] for v in variables(timeline, row['path'])], ['0.0', '1.0', '0.0'])
        self.assertEqual(vm.checkpoint(), before)

    def test_link_payloads_are_paged_from_the_selected_snapshot_without_allocating(self):
        frame = spatial.CoordinateFrame('@0:0', 'meters')
        point = spatial.SpatialValue('Point3', frame, (0.0, 0.0, 0.0))
        world = spatial.SpaceValue('graph', frame, 'str', 'int[]').insert("a", point, 'vertex')
        world = world.connect("edge", world.entity_id("a"), world.entity_id("a"), spatial.FrozenValue('int[]', (3, 5, 8)))
        timeline = timeline_for(spatial_program())
        timeline.vm.stack.append(world)
        before = timeline.vm.checkpoint()
        fields = {item['name']: item for item in variables(timeline, ['stack', 0])}
        edge = variables(timeline, fields['links']['path'])[0]
        self.assertEqual(edge['type'], 'Link[World, string, [int]]')
        data = {item['name']: item for item in variables(timeline, edge['path'])}
        self.assertEqual(data['source']['type'], 'EntityId[World, string]')
        self.assertEqual({v['name']: v['value'] for v in variables(timeline, data['source']['path'])}['key'], '"a"')
        self.assertEqual(data['target']['type'], 'EntityId[World, string]')
        self.assertEqual([item['value'] for item in variables(timeline, data['payload']['path'], start=1, count=1)], ['5'])
        self.assertEqual(timeline.vm.checkpoint(), before)

    def test_frozen_map_keys_are_not_treated_as_enum_metadata(self):
        code = delay_program()
        code['enums']['Option<int>'] = {'Some': {'value': 'int'}, 'None': {}}
        timeline = timeline_for(code)
        timeline.vm.stack.extend([
            spatial.FrozenValue('map<str,int>', (('$variant', 9), ('value', 10))),
            spatial.FrozenValue('Option<int>', (('$variant', 'Some'), ('value', 11))),
        ])
        before = timeline.vm.checkpoint()
        items = variables(timeline, ['stack'])
        self.assertEqual([v['name'] for v in variables(timeline, items[0]['path'])], ['$variant', 'value'])
        self.assertIn('.Some', items[1]['value'])
        self.assertEqual([(v['name'], v['value']) for v in variables(timeline, items[1]['path'])], [('value', '11')])
        self.assertEqual(timeline.vm.checkpoint(), before)

    def test_typed_maps_expand_keys_values_and_snapshots_without_mutation(self):
        code = delay_program()
        code['enums']['@0:1'] = {'Code': {'value': 'int'}}
        timeline = timeline_for(code)
        vm = timeline.vm
        key = vm.allocate('@0:1', {'$variant': 'Code', 'value': 7}, None)
        table = vm.allocate('map<@0:1,int[]>', [(key, vm.allocate('int[]', [2, 3], None))], None)
        text = vm.allocate('map<str,int>', [('$variant', 9), ('value', 10)], None)
        frozen = vm.freeze_spatial_payload(table, None)
        vm.stack.extend([table, frozen, text])
        before = vm.checkpoint()
        for index in (0, 1):
            entries = variables(timeline, ['stack', index], start=0, count=1)
            self.assertEqual(entries[0]['name'], '0')
            fields = {item['name']: item for item in variables(timeline, entries[0]['path'])}
            self.assertEqual(variables(timeline, fields['key']['path'])[0]['value'], '7')
            self.assertEqual([item['value'] for item in variables(timeline, fields['value']['path'])], ['2', '3'])
            json.dumps(entries)
            json.dumps(list(fields.values()))
        self.assertEqual([item['name'] for item in variables(timeline, ['stack', 2])], ['$variant', 'value'])
        self.assertEqual(variables(timeline, ['stack', 0], start=1, count=1), [])
        self.assertEqual(vm.checkpoint(), before)

    def test_closure_capture_names_do_not_shift_requested_page(self):
        code = delay_program()
        code['functions']['main']['locals'] = 2
        code['functions']['closure'] = dict(params=['int'], captures=['int', 'int'], locals=3,
                                           result='int', instructions=[dict(op='LOAD', arg=1), dict(op='RETURN')])
        timeline = timeline_for(code)
        timeline.compiled.debug['functions']['closure']['names'] = ['argument', 'first', 'second']
        vm = timeline.vm
        vm.call_stack[0].memory[0] = 10
        vm.call_stack[0].memory[1] = 20
        first = vm.capture_value(vm.call_stack[0], 0)
        second = vm.capture_value(vm.call_stack[0], 1)
        callback = vm.allocate('fn(int)->(int)', {'$function': 'closure', '$capture0': first, '$capture1': second}, None)
        vm.stack.append(callback)
        values = variables(timeline, ['stack', 0], start=0, count=1)
        self.assertEqual([(v['name'], v['value']) for v in values], [('first', '10')])
        values = variables(timeline, ['stack', 0], start=1, count=1)
        self.assertEqual([(v['name'], v['value']) for v in values], [('second', '20')])

    def test_flow_scopes_follow_helpers_and_keep_value_readiness(self):
        vm = VM(pipeline())
        vm.step(snapshot=False)
        vm.step(snapshot=False)
        self.assertEqual(flow_scopes(vm), [dict(frame=0, name='Pipeline', path=['flow', 0])])
        self.assertEqual(flow_values(vm, 0)['values']['pass']['phase'], 'pending')
        vm.step(snapshot=False)
        vm.step(snapshot=False)
        scopes = {item['frame']: item for item in flow_scopes(vm)}
        self.assertEqual(scopes[0]['path'], ['flow', 1])
        self.assertEqual(scopes[1]['path'], ['flow', 1])
        before = vm.checkpoint()
        view = flow_values(vm, 1)
        self.assertEqual(view['inputs'], {'value': 3})
        self.assertEqual(view['values']['pass']['phase'], 'running')
        self.assertEqual(vm.checkpoint(), before)
        with self.assertRaises(PixelError): flow_values(vm, 0)
        with self.assertRaises(PixelError): flow_values(vm, True)

    def test_workstation_flow_variables_use_historical_state(self):
        timeline = timeline_for(delay_program())
        while not (timeline.vm.flow_runtime.transaction and timeline.vm.flow_runtime.transaction.staged):
            timeline.advance()
        selected = timeline.cursor
        while not timeline.vm.halted: timeline.advance()
        self.assertEqual(timeline.vm.flow_runtime.instances[0].steps, 2)
        timeline.seek(selected)
        service = Workstation()
        self.addCleanup(service.compiler.close)
        service.sessions['native'] = timeline
        status = service.status(timeline)
        self.assertEqual(status['flow_scopes'], [dict(frame=0, name='Delay', path=['flow', 0])])
        before = timeline.replay_vm.checkpoint()
        args = dict(session='native', z=selected)
        fields = {v['name']: v for v in service.request('variables', dict(args, path=['flow', 0]))['variables']}
        old = service.request('variables', dict(args, path=fields['oldState']['path']))['variables']
        self.assertEqual([(v['name'], v['value']) for v in old], [('previous', '0')])
        updates = variables(timeline, fields['nextState']['path'])
        update = {v['name']: v['value'] for v in variables(timeline, updates[0]['path'])}
        self.assertEqual(update, {'staged': 'true', 'retained': 'false', 'value': '5'})
        handle = variables(timeline, ['frame', 1])[0]
        instance = {v['name']: v for v in variables(timeline, handle['path'])}
        self.assertEqual(instance['step']['value'], '0')
        self.assertEqual(variables(timeline, instance['state']['path'])[0]['value'], '0')
        self.assertEqual(timeline.replay_vm.checkpoint(), before)
        self.assertEqual(timeline.vm.flow_runtime.instances[0].steps, 2)
        service.request('seek', dict(session='native', z=0))
        with self.assertRaisesRegex(PixelError, 'different time'):
            service.request('variables', dict(args, path=['flow', 0]))
