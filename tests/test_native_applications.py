"""Acceptance scenarios for native order transitions."""
import unittest
import tempfile
from pathlib import Path

from pixellang.native_events import NativeApplicationRuntime, NativeEventRuntime
from pixellang.model import PixelError
from pixellang.vm import VM
from pixellang.event_journal import JournaledApplicationRuntime
from tests.native_compiler import native_service

ROOT = Path(__file__).resolve().parents[1]


class NativeApplicationsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = native_service()
        cls.addClassCleanup(cls.compiler.close)
        cls.codes = {}
        library = {'std/' + p.name: p.read_text() for p in (ROOT/'pixellang/stdlib').glob('*.pxl')}
        for name in ('native-orders',):
            files = {**library, **{p.name:p.read_text() for p in (ROOT/'examples'/name).glob('*.pxl')}}
            response = cls.compiler.request(dict(version=1, operation='compile', files=files, entry='main.pxl'))
            if response['status'] != 'ok': raise AssertionError(response)
            cls.codes[name] = response['result']['bytecode']

    def runtime(self, name): return NativeEventRuntime(VM(self.codes[name]))

    def test_business_events_without_clock_survive_png_and_replay(self):
        source = """enum Event { Add(amount: int), Reset }
flow Counter(initial: int) step(event: Event) -> int {
 state count = initial
 let updated = match event { .Add(amount) => count + amount, .Reset => initial }
 next count = updated
 output updated
}
effect fn main() { Counter(10) }
"""
        response = self.compiler.request(dict(version=1, operation='export-png', files={'main.pxl': source}))
        self.assertEqual(response['status'], 'ok', response)
        response = self.compiler.request(dict(version=1, operation='compile-png', image=response['result']['image']))
        self.assertEqual(response['status'], 'ok', response)
        code = response['result']['bytecode']
        runtime = NativeEventRuntime(VM(code))
        for event, expected in ((dict(variant='Add', fields={'amount': 5}), 15),
                                (dict(variant='Reset', fields={}), 10)):
            runtime.enqueue(event)
            self.assertEqual(runtime.step(), expected)
        restored = NativeEventRuntime.restore(VM(code), runtime.recording())
        self.assertEqual(restored.view(), 10)
        self.assertEqual(restored.vm.checkpoint(), runtime.vm.checkpoint())

    def test_readonly_configuration_survives_journal_checkpoint(self):
        files = {'std/' + p.name: p.read_text() for p in (ROOT/'pixellang/stdlib').glob('*.pxl')}
        files['main.pxl'] = """import "std/time.pxl" as time
enum Event { Tick }
flow Counter(config: [int]) step(event: Event, clock: time.Step) -> int {
 state count = 0
 let updated = count + config[0]
 next count = updated
 output updated
}
effect fn main() { Counter([3]) }
"""
        response = self.compiler.request(dict(version=1, operation='compile', files=files))
        self.assertEqual(response['status'], 'ok', response)
        code = response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'config.pxjournal'
            runtime = JournaledApplicationRuntime(VM(code), path, checkpoint_interval=1)
            try:
                self.assertEqual(runtime.step(), 3)
                self.assertEqual(runtime.step(), 6)
                configuration = runtime.vm.flow_runtime.instances[0].configuration
            finally:
                runtime.close()
            resumed = JournaledApplicationRuntime(VM(code), path, checkpoint_interval=1)
            try:
                self.assertEqual(resumed.view(), 6)
                self.assertEqual(resumed.vm.flow_runtime.instances[0].configuration, configuration)
                self.assertEqual(resumed.step(), 9)
            finally:
                resumed.close()

    def test_persistent_checkpoints_resume_typed_order(self):
        for name, event in (('native-orders', dict(variant='Pay', fields={'cents':1299})),):
            with self.subTest(application=name), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'checkpoint.pxjournal'
                reference = NativeApplicationRuntime(VM(self.codes[name], max_heap_items=512))
                journal = JournaledApplicationRuntime(VM(self.codes[name], max_heap_items=512), path, checkpoint_interval=4)
                try:
                    for _ in range(20):
                        self.assertEqual(reference.step(), journal.step())
                    reference.enqueue(event)
                    journal.enqueue(event)
                    before = journal.view()
                finally:
                    journal.close()
                resumed = JournaledApplicationRuntime(VM(self.codes[name], max_heap_items=512), path, checkpoint_interval=4)
                try:
                    self.assertEqual(resumed.view(), before)
                    self.assertEqual(resumed.pending, reference.pending)
                    self.assertEqual(resumed.vm.flow_runtime.instances[0].cells,
                                     reference.vm.flow_runtime.instances[0].cells)
                    for _ in range(15):
                        self.assertEqual(resumed.step(), reference.step())
                finally:
                    resumed.close()

    def test_journal_recovers_real_order_state_and_pending_inputs(self):
        for name, event in (('native-orders', dict(variant='Pay', fields={'cents': 1299})),):
            with self.subTest(application=name), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'run.pxjournal'
                runtime = JournaledApplicationRuntime(VM(self.codes[name], max_heap_items=512), path)
                try:
                    runtime.step()
                    runtime.enqueue(event)
                    before = runtime.vm.checkpoint()
                    pending = list(runtime.pending)
                finally:
                    runtime.close()
                restored = JournaledApplicationRuntime(VM(self.codes[name], max_heap_items=512), path)
                try:
                    self.assertEqual(restored.vm.checkpoint(), before)
                    self.assertEqual(restored.pending, pending)
                    self.assertEqual(restored.inspect(1).vm.checkpoint(), before)
                    result = restored.step()
                    self.assertEqual(result['order']['status']['variant'], 'Paid')
                    after = restored.vm.checkpoint()
                finally:
                    restored.close()
                again = JournaledApplicationRuntime(VM(self.codes[name], max_heap_items=512), path)
                try:
                    self.assertEqual(again.vm.checkpoint(), after)
                    self.assertEqual(again.completed, 2)
                    self.assertEqual(again.pending, [])
                finally:
                    again.close()

    def test_order_continue_without_retaining_history(self):
        for name in ('native-orders',):
            with self.subTest(application=name):
                runtime = NativeApplicationRuntime(VM(self.codes[name], max_heap_items=512))
                for _ in range(4200):
                    runtime.step()
                self.assertEqual(runtime.completed, 4200)
                self.assertEqual(runtime.view()['order']['status']['variant'], 'Expired')
                self.assertFalse(hasattr(runtime, 'records'))
                self.assertLess(runtime.vm.spatial_items, 512)

    def send(self, runtime, variant, **fields):
        runtime.enqueue(dict(variant=variant, fields=fields))
        return runtime.step()

    def test_order_success_invalid_transition_and_instance_isolation(self):
        order, other = self.runtime('native-orders'), self.runtime('native-orders')
        order.step(); other.step()
        self.assertEqual(self.send(order, 'Pay', cents=1299)['order']['status']['variant'], 'Paid')
        paid = order.view()['order']
        completed = order.completed
        rejected = self.send(order, 'Pay', cents=1299)
        self.assertEqual(rejected['order'], paid)
        self.assertEqual(rejected['outcome']['variant'], 'Err')
        self.assertEqual(rejected['outcome']['fields']['error']['variant'], 'InvalidTransition')
        self.assertEqual(order.completed, completed + 1)
        self.assertEqual(order.pending, [])
        self.assertEqual(self.send(order, 'Ship', tracking='PK-001')['order']['status'],
                         dict(variant='Shipped', fields={'tracking':'PK-001'}))
        self.assertEqual(other.view()['order']['status']['variant'], 'AwaitingPayment')
        replay = NativeEventRuntime.restore(VM(self.codes['native-orders']), order.recording())
        self.assertEqual(replay.vm.checkpoint(), order.vm.checkpoint())

    def test_order_expiry_and_wrong_amount(self):
        order = self.runtime('native-orders'); order.step()
        original = order.view()['order']
        rejected = self.send(order, 'Pay', cents=1)
        self.assertEqual(rejected['order'], original)
        self.assertEqual(rejected['outcome']['fields']['error'],
                         dict(variant='AmountMismatch', fields=dict(expected=1299, actual=1)))
        self.assertEqual(order.pending, [])
        for _ in range(30): order.step()
        self.assertEqual(order.view()['order']['status']['variant'], 'Expired')
        rejected = self.send(order, 'Pay', cents=1299)
        self.assertEqual(rejected['outcome']['fields']['error']['variant'], 'PaymentExpired')
        self.assertEqual(order.pending, [])

    def test_order_execution_fault_retains_event_and_rolls_back(self):
        order = self.runtime('native-orders')
        order.step()
        order.enqueue(dict(variant='Pay', fields={'cents': 1299}))
        before = order.vm.checkpoint()
        pending = list(order.pending)
        completed = order.completed
        budget = order.step_budget
        order.step_budget = 1
        with self.assertRaises(PixelError):
            order.step()
        self.assertEqual(order.vm.checkpoint(), before)
        self.assertEqual(order.pending, pending)
        self.assertEqual(order.completed, completed)
        order.step_budget = budget
        self.assertEqual(order.step()['order']['status']['variant'], 'Paid')

    def test_order_rejection_survives_journal_restart_without_blocking_next_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rejections.pxjournal'
            runtime = JournaledApplicationRuntime(VM(self.codes['native-orders']), path, checkpoint_interval=1)
            try:
                runtime.step()
                original = runtime.view()['order']
                runtime.enqueue(dict(variant='Pay', fields={'cents': 1}))
                runtime.enqueue(dict(variant='Pay', fields={'cents': 1299}))
                rejected = runtime.step()
                self.assertEqual(rejected['order'], original)
                self.assertEqual(rejected['outcome']['fields']['error']['variant'], 'AmountMismatch')
                self.assertEqual(len(runtime.pending), 1)
                completed = runtime.completed
            finally:
                runtime.close()
            restored = JournaledApplicationRuntime(VM(self.codes['native-orders']), path, checkpoint_interval=1)
            try:
                self.assertEqual(restored.view(), rejected)
                self.assertEqual(restored.completed, completed)
                self.assertEqual(restored.step()['order']['status']['variant'], 'Paid')
                self.assertEqual(restored.pending, [])
            finally:
                restored.close()

    def test_order_png_recovery_preserves_typed_rejection_and_continuation(self):
        files = {'std/' + p.name: p.read_text() for p in (ROOT/'pixellang/stdlib').glob('*.pxl')}
        files.update({p.name: p.read_text() for p in (ROOT/'examples/native-orders').glob('*.pxl')})
        response = self.compiler.request(dict(version=1, operation='export-png', files=files, entry='main.pxl'))
        self.assertEqual(response['status'], 'ok', response)
        recovered = self.compiler.request(dict(version=1, operation='recover-png', image=response['result']['image']))
        self.assertEqual(recovered['status'], 'ok', recovered)
        compiled = self.compiler.request(dict(version=1, operation='compile', **recovered['result']))
        self.assertEqual(compiled['status'], 'ok', compiled)
        reference = self.runtime('native-orders')
        restored = NativeEventRuntime(VM(compiled['result']['bytecode']))
        self.assertEqual(reference.step(), restored.step())
        for event in [dict(variant='Pay', fields={'cents': 1}),
                      dict(variant='Pay', fields={'cents': 1299}),
                      dict(variant='Ship', fields={'tracking': ''}),
                      dict(variant='Ship', fields={'tracking': 'PK-001'})]:
            reference.enqueue(event)
            restored.enqueue(event)
            self.assertEqual(reference.step(), restored.step())
        self.assertEqual(restored.view()['order']['status']['variant'], 'Shipped')
        self.assertEqual(restored.pending, [])

    def test_selected_event_instruction_debugging_is_isolated_and_reversible(self):
        from pixellang.application_debug import EventTimeline
        from pixellang.debug_artifact import prepare_debug
        order = self.runtime('native-orders'); order.step()
        self.send(order, 'Pay', cents=1299)
        before = order.vm.checkpoint()
        files = {'std/' + p.name:p.read_text() for p in (ROOT/'pixellang/stdlib').glob('*.pxl')}
        files.update({p.name:p.read_text() for p in (ROOT/'examples/native-orders').glob('*.pxl')})
        response = self.compiler.request(dict(version=1,operation='debug-bundle',files=files,entry='main.pxl'))
        self.assertEqual(response['status'], 'ok', response)
        result = response['result']
        compiled = prepare_debug(result['bytecode'], result['debug'], result['document'])
        compiled.source_project = dict(files=files,entry='main.pxl')
        timeline = EventTimeline(compiled, order, 2)
        initial = timeline.vm.checkpoint()
        timeline.run()
        self.assertTrue(timeline.vm.halted)
        self.assertIsNone(timeline.vm.error)
        self.assertGreater(len(timeline.events), 5)
        self.assertEqual(timeline.vm.materialize(timeline.vm.stack[-1])['order']['status']['variant'], 'Paid')
        timeline.seek(0)
        self.assertEqual(timeline.replay_vm.checkpoint(), initial)
        timeline.seek(len(timeline.events)-1)
        self.assertEqual(order.vm.checkpoint(), before)
        self.assertEqual(order.cursor, 2)
        with tempfile.TemporaryDirectory() as directory:
            journal = JournaledApplicationRuntime(VM(self.codes['native-orders']), Path(directory) / 'orders.pxjournal', checkpoint_interval=1)
            try:
                journal.step()
                journal.enqueue(dict(variant='Pay', fields={'cents':1299}))
                journal.step()
                before = journal.vm.checkpoint()
                revision = journal.journal.revision
                debug = EventTimeline(compiled, journal, 2)
                initial = debug.vm.checkpoint()
                debug.run()
                self.assertIsNone(debug.vm.error)
                self.assertEqual(debug.vm.materialize(debug.vm.stack[-1])['order']['status']['variant'], 'Paid')
                debug.seek(0)
                self.assertEqual(debug.replay_vm.checkpoint(), initial)
                self.assertEqual(journal.vm.checkpoint(), before)
                self.assertEqual(journal.journal.revision, revision)
            finally:
                journal.close()
