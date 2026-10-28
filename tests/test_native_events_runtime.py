from copy import deepcopy
import unittest

from pixellang.model import PixelError
from pixellang.native_events import NativeApplicationRuntime, NativeEventRuntime
from pixellang.vm import VM, validate_bytecode
from tests.test_native_flow_runtime import delay_program


def application_program():
    code = delay_program()
    def op(name, arg=None, node=0):
        return dict(op=name, arg=arg, node=node, module=0)
    code['records'].update(Duration={'nanoseconds': 'int'},
                           Clock={'index': 'int', 'elapsed': 'Duration', 'delta': 'Duration'})
    code['enums'] = {'Event': {'Tick': {}, 'Set': {'value': 'int'}}}
    graph = code['flows']['delay']
    graph.update(stateful=False, inputs=[dict(name='event', node=1, slot=0, type='Event'),
                                        dict(name='clock', node=2, slot=1, type='Clock')],
                 states=[], updates=[], dependencies=[dict(source=2, target=6, reference=5)],
                 output=dict(node=5, slot=2, type='int'))
    code['functions']['delay'] = dict(params=['Event', 'Clock'], locals=3, result='int',
        instructions=[op('LOAD', 1, 5), op('FIELD', 'index', 5),
                      op('STORE', 2, 5), op('LOAD', 2, 5), op('RETURN', None, 6)])
    del code['functions']['initial']
    code['constants'] = [dict(type='int', value=0)]
    code['functions']['main'] = dict(params=[], locals=0, result='unit', instructions=[
        op('FLOW_NEW', 'delay'), op('POP'), op('PUSH', 0), op('RETURN')])
    def nominal(value):
        if isinstance(value, str):
            return value.replace('Duration', '@0:1').replace('Clock', '@0:2').replace('Event', '@0:3')
        if isinstance(value, dict): return {nominal(k): nominal(v) for k, v in value.items()}
        if isinstance(value, list): return [nominal(v) for v in value]
        return value
    return nominal(code)


def event_only_program():
    code = application_program()
    code['enums']['@0:3'].pop('Tick')
    graph = code['flows']['delay']
    graph['inputs'] = graph['inputs'][:1]
    graph['dependencies'] = [dict(source=1, target=6, reference=5)]
    graph['output'] = dict(node=5, slot=1, type='@0:3')
    def op(name, arg=None, node=5):
        return dict(op=name, arg=arg, node=node, module=0)
    code['functions']['delay'] = dict(params=['@0:3'], locals=2, result='@0:3',
        instructions=[op('LOAD', 0), op('STORE', 1), op('LOAD', 1), op('RETURN', node=6)])
    return code


class EventOnlyRuntimeTests(unittest.TestCase):
    def test_events_need_no_clock_or_tick_and_replay_without_synthetic_events(self):
        runtime = NativeEventRuntime(VM(event_only_program()))
        self.assertEqual(runtime.configuration, {'instance': 0})
        before = runtime.vm.checkpoint()
        with self.assertRaisesRegex(PixelError, 'No pending'):
            runtime.step()
        self.assertEqual(runtime.vm.checkpoint(), before)
        for n in (7, 9):
            event = dict(variant='Set', fields={'value': n})
            runtime.enqueue(event)
            self.assertEqual(runtime.step(), event)
        self.assertEqual([r.kind for r in runtime.records], ['input', 'input'])
        restored = NativeEventRuntime.restore(VM(event_only_program()), runtime.recording())
        self.assertEqual(restored.vm.checkpoint(), runtime.vm.checkpoint())
        self.assertEqual(restored.seek(0), None)
        self.assertEqual(restored.seek(2), runtime.view())

    def test_failed_event_keeps_queue_and_rejects_fabricated_timer_record(self):
        runtime = NativeEventRuntime(VM(event_only_program()), step_budget=1)
        event = dict(variant='Set', fields={'value': 2})
        runtime.enqueue(event)
        checkpoint, pending = runtime.vm.checkpoint(), deepcopy(runtime.pending)
        with self.assertRaises(PixelError): runtime.step()
        self.assertEqual(runtime.vm.checkpoint(), checkpoint)
        self.assertEqual(runtime.pending, pending)
        self.assertEqual(runtime.completed, 0)
        runtime.step_budget = 1000
        self.assertEqual(runtime.step(), event)
        recording = runtime.recording()
        recording['events'][0]['kind'] = 'tick'
        with self.assertRaisesRegex(PixelError, 'timer events'):
            NativeEventRuntime.restore(VM(event_only_program()), recording)

    def test_event_only_rejects_timer_configuration_and_future_inputs(self):
        for configuration in ({'intervalNanoseconds': 10}, {'tick': {'variant': 'Set', 'fields': {'value': 1}}}):
            with self.assertRaisesRegex(PixelError, 'timer configuration'):
                NativeEventRuntime(VM(event_only_program()), configuration=configuration)
        runtime = NativeEventRuntime(VM(event_only_program()))
        with self.assertRaisesRegex(PixelError, 'schedule inputs'):
            runtime.enqueue(dict(variant='Set', fields={'value': 1}), tick=1)

    def test_journal_restart_restores_event_only_checkpoint(self):
        from pixellang.event_journal import JournaledApplicationRuntime
        from pathlib import Path
        import tempfile
        event = dict(variant='Set', fields={'value': 4})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'events.pxjournal'
            runtime = JournaledApplicationRuntime(VM(event_only_program()), path, checkpoint_interval=1)
            try:
                runtime.enqueue(event)
                self.assertEqual(runtime.step(), event)
            finally:
                runtime.close()
            restored = JournaledApplicationRuntime(VM(event_only_program()), path, checkpoint_interval=1)
            try:
                self.assertEqual(restored.view(), event)
                restored.enqueue(event)
                self.assertEqual(restored.step(), event)
            finally:
                restored.close()


class NativeEventRuntimeTests(unittest.TestCase):
    def test_host_configuration_is_preserved_by_recording_replay(self):
        configuration = dict(intervalNanoseconds=7,
                             tick={'variant': 'Set', 'fields': {'value': 42}})
        runtime = NativeEventRuntime(VM(application_program()), configuration=configuration)
        configuration['tick']['fields']['value'] = 99
        runtime.step()
        runtime.step()
        self.assertEqual(runtime.records[-1].delta_ns, 7)
        self.assertEqual(runtime.configuration['tick']['fields']['value'], 42)
        document = runtime.recording()
        restored = NativeEventRuntime.restore(VM(application_program()), document)
        self.assertEqual(restored.configuration, runtime.configuration)
        self.assertEqual(restored.vm.checkpoint(), runtime.vm.checkpoint())
        self.assertEqual(restored.step(), runtime.step())

    def test_host_rejects_ambiguous_instances_and_invalid_configuration(self):
        code = application_program()
        instructions = code['functions']['main']['instructions']
        instructions[:0] = deepcopy(instructions[:2])
        with self.assertRaisesRegex(PixelError, 'exactly one'):
            NativeApplicationRuntime(VM(code))
        runtime = NativeApplicationRuntime(VM(code), configuration={'instance': 1})
        runtime.step()
        self.assertEqual(runtime.vm.flow_runtime.instances[0].steps, 0)
        self.assertEqual(runtime.vm.flow_runtime.instances[1].steps, 1)
        for configuration in ({'instance': True}, {'instance': 5}, {'unknown': 1},
                              {'intervalNanoseconds': True}, {'intervalNanoseconds': 1 << 63},
                              {'tick': {'variant': 'Missing', 'fields': {}}}):
            with self.subTest(configuration=configuration), self.assertRaises(PixelError):
                NativeApplicationRuntime(VM(application_program()), configuration=configuration)

    def test_execution_and_recording_share_event_order_and_native_state(self):
        execution = NativeApplicationRuntime(VM(application_program()))
        recording = NativeEventRuntime(VM(application_program()))
        for runtime in (execution, recording):
            runtime.enqueue({'variant': 'Set', 'fields': {'value': 10}}, tick=1)
            runtime.enqueue({'variant': 'Set', 'fields': {'value': 20}}, tick=1)
        for _ in range(130):
            self.assertEqual(execution.step(), recording.step())
            self.assertEqual(execution.vm.checkpoint(), recording.vm.checkpoint())
        self.assertEqual(execution.completed, 130)
        self.assertEqual(execution.latest, recording.records[-1])
        self.assertFalse(hasattr(execution, 'records'))
        self.assertFalse(hasattr(execution, 'checkpoints'))

    def test_execution_continues_past_recording_limit_without_retaining_events(self):
        runtime = NativeApplicationRuntime(VM(application_program(), max_heap_items=128))
        for _ in range(5000):
            runtime.step()
        self.assertEqual(runtime.completed, 5000)
        self.assertEqual(runtime.view(), 4999)
        self.assertEqual(runtime.vm.flow_runtime.instances[0].steps, 5000)
        self.assertEqual(runtime.pending, [])
        self.assertFalse(hasattr(runtime, 'records'))
        self.assertLess(runtime.vm.spatial_items, 128)

    def test_native_accounting_traces_shared_snapshots_and_rejects_live_growth(self):
        from pixellang.spatial_values import CoordinateFrame, SpaceValue, SpatialValue, FrozenValue
        runtime = NativeApplicationRuntime(VM(application_program(), max_heap_items=128))
        frame = CoordinateFrame('@0:7', 'meters')
        point = SpatialValue('Point3', frame, (0.0, 0.0, 0.0))
        scene = SpaceValue('scene', frame, 'int[]', 'str').insert(point, FrozenValue('int[]', (1, 2)))
        runtime.vm.stack.extend((scene, scene))
        runtime.vm.recount_spatial()
        shared = runtime.vm.spatial_items
        runtime.vm.stack.pop()
        runtime.vm.recount_spatial()
        self.assertEqual(runtime.vm.spatial_items, shared)
        runtime.vm.stack.pop()
        runtime.vm.recount_spatial()
        self.assertLess(runtime.vm.spatial_items, shared)
        runtime.vm.stack.append(FrozenValue('int[]', tuple(range(256))))
        with self.assertRaisesRegex(PixelError, 'Live spatial state'):
            runtime.vm.recount_spatial()

    def test_execution_failure_preserves_scheduler_queue_and_state(self):
        runtime = NativeApplicationRuntime(VM(application_program()))
        runtime.step()
        runtime.enqueue({'variant': 'Set', 'fields': {'value': 20}})
        before = runtime.vm.checkpoint()
        pending = deepcopy(runtime.pending)
        latest = runtime.latest
        runtime.step_budget = 1
        with self.assertRaises(PixelError):
            runtime.step()
        self.assertEqual(runtime.vm.checkpoint(), before)
        self.assertEqual(runtime.pending, pending)
        self.assertIs(runtime.latest, latest)
        self.assertEqual(runtime.completed, 1)
        runtime.step_budget = 1_000_000
        runtime.step()
        self.assertEqual(runtime.completed, 2)
        self.assertEqual(runtime.pending, [])

    def test_fixed_clock_and_fifo_inputs_have_distinct_event_and_tick_counts(self):
        runtime = NativeEventRuntime(VM(application_program()))
        runtime.enqueue({'variant': 'Set', 'fields': {'value': 10}}, tick=1)
        runtime.enqueue({'variant': 'Set', 'fields': {'value': 20}}, tick=1)
        self.assertEqual([runtime.step() for _ in range(5)], [0, 1, 1, 1, 2])
        self.assertEqual([record.kind for record in runtime.records], ['tick', 'tick', 'input', 'input', 'tick'])
        self.assertEqual([record.delta_ns for record in runtime.records], [0, 100_000_000, 0, 0, 100_000_000])
        self.assertLess(runtime.records[2].sequence, runtime.records[3].sequence)
        self.assertEqual(runtime.vm.flow_runtime.instances[0].steps, 5)

    def test_input_validation_does_not_consume_queue_or_vm_state(self):
        runtime = NativeEventRuntime(VM(application_program()))
        before = runtime.vm.checkpoint()
        with self.assertRaises(PixelError):
            runtime.enqueue({'variant': 'Set', 'fields': {'value': 'wrong'}})
        self.assertEqual(runtime.vm.checkpoint(), before)
        self.assertEqual((runtime.sequence, runtime.pending), (0, []))
        runtime.enqueue({'variant': 'Set', 'fields': {'value': 3}})
        self.assertEqual(runtime.vm.checkpoint(), before)

    def test_history_replay_restores_native_vm_and_blocks_historical_writes(self):
        runtime = NativeEventRuntime(VM(application_program()))
        runtime.enqueue({'variant': 'Set', 'fields': {'value': 3}}, tick=1)
        for _ in range(70):
            runtime.step()
        final = runtime.vm.checkpoint()
        self.assertEqual(runtime.seek(2), 1)
        with self.assertRaises(PixelError): runtime.step()
        with self.assertRaises(PixelError): runtime.enqueue({'variant': 'Tick', 'fields': {}})
        runtime.seek(70)
        self.assertEqual(runtime.vm.checkpoint(), final)
        self.assertEqual(runtime.step(), 69)

    def test_failure_and_history_budget_restore_entire_transaction(self):
        for options in (dict(step_budget=1), dict(max_history_bytes=1)):
            with self.subTest(options=options):
                runtime = NativeEventRuntime(VM(application_program()), **options)
                before = runtime.vm.checkpoint()
                with self.assertRaises(PixelError): runtime.step()
                self.assertEqual(runtime.vm.checkpoint(), before)
                self.assertEqual((runtime.tick, runtime.cursor, runtime.sequence), (-1, 0, 0))
                self.assertEqual(runtime.records, [])

    def test_loader_rejects_invalid_clock_contract_and_interval(self):
        bad = application_program()
        bad['records']['@0:2']['delta'] = 'int'
        validate_bytecode(bad)
        with self.assertRaises(PixelError): NativeEventRuntime(VM(bad))
        with self.assertRaises(PixelError): NativeEventRuntime(VM(application_program()), configuration={'intervalNanoseconds': 0})
        self.assertEqual(VM(application_program()).run(), [])

    def test_logical_time_overflow_leaves_history_and_vm_unchanged(self):
        runtime = NativeEventRuntime(VM(application_program()), configuration={'intervalNanoseconds': (1 << 63) - 1})
        runtime.step()
        runtime.step()
        before = runtime.vm.checkpoint()
        records = deepcopy(runtime.records)
        with self.assertRaisesRegex(PixelError, 'Logical time'):
            runtime.step()
        self.assertEqual(runtime.vm.checkpoint(), before)
        self.assertEqual(runtime.records, records)

    def test_recording_reexecutes_native_inputs_and_preserves_pending_and_cursor(self):
        runtime = NativeEventRuntime(VM(application_program()))
        runtime.enqueue({'variant': 'Set', 'fields': {'value': 9223372036854775807}}, tick=1)
        runtime.enqueue({'variant': 'Set', 'fields': {'value': 8}}, tick=100)
        for _ in range(70): runtime.step()
        runtime.seek(3)
        document = runtime.recording()
        restored = NativeEventRuntime.restore(VM(application_program()), document)
        self.assertEqual(restored.recording(), document)
        self.assertEqual(restored.vm.checkpoint(), runtime.vm.checkpoint())
        runtime.seek(70)
        restored.seek(70)
        self.assertEqual(runtime.step(), restored.step())
        self.assertEqual(runtime.vm.checkpoint(), restored.vm.checkpoint())

    def test_invalid_recordings_are_rejected_and_pending_inputs_can_be_discarded(self):
        runtime = NativeEventRuntime(VM(application_program()))
        sequence = runtime.enqueue({'variant': 'Set', 'fields': {'value': 5}})
        runtime.discard(sequence)
        self.assertEqual((runtime.pending, runtime.pending_bytes), ([], 0))
        runtime.step()
        for change in ({'program': 'invalid'}, {'cursor': 3},
                       {'events': [dict(tick=0, kind='tick', input=None, output=9)]},
                       {'events': [dict(tick=1, kind='tick', input=None, output=1)]}):
            with self.assertRaises(PixelError):
                NativeEventRuntime.restore(VM(application_program()), {**runtime.recording(), **change})

    def test_instruction_debugger_reports_application_boundary_without_spinning(self):
        from pixellang.temporal import Timeline
        timeline = object.__new__(Timeline)
        timeline.vm = NativeEventRuntime(VM(application_program())).vm
        with self.assertRaisesRegex(PixelError, 'waiting for events'):
            timeline.advance()

    def test_checkpoint_budget_and_display_do_not_consume_program_output_budget(self):
        from pixellang.recording import footprint
        initial = NativeEventRuntime(VM(application_program(), max_output_chars=1))
        budget = footprint(initial.checkpoints[0]) * 3
        runtime = NativeEventRuntime(VM(application_program(), max_output_chars=1), max_checkpoint_bytes=budget)
        for _ in range(200): runtime.step()
        self.assertEqual(runtime.vm.output_budget, 1)
        self.assertLessEqual(runtime.checkpoint_bytes, budget)
        final = runtime.vm.checkpoint()
        runtime.seek(3);runtime.seek(200)
        self.assertEqual(runtime.vm.checkpoint(), final)

    def test_input_inspection_has_its_own_budget_and_does_not_change_execution(self):
        runtime = NativeApplicationRuntime(VM(application_program(), max_output_chars=1))
        runtime.enqueue({'variant': 'Set', 'fields': {'value': 9223372036854775807}})
        before = runtime.vm.checkpoint()
        self.assertEqual(runtime.input_value(runtime.pending[0][2]),
                         {'variant': 'Set', 'fields': {'value': 9223372036854775807}})
        self.assertEqual(runtime.vm.checkpoint(), before)
