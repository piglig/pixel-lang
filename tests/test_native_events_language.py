"""Native event registration, execution and pixel delivery from PixelLang source."""
from tests.native_compiler import native_service
from pathlib import Path
import unittest

from pixellang.model import PixelError
from pixellang.native_events import NativeEventRuntime
from pixellang.vm import VM


APPLICATION = '''import "std/time.pxl" as time
enum Event { Tick, Add(value: int), Reject }
record View { value: int, tick: int, elapsed: int, delta: int }
fn Update(previous: int, event: Event, clock: time.Step) -> View {
 var value = previous
 match event {
  Event.Tick => {},
  Event.Add(amount) => { value += amount },
  Event.Reject => { assert(false) }
 }
 return View{value, tick: clock.index, elapsed: clock.elapsed.nanoseconds, delta: clock.delta.nanoseconds}
}
flow Counter() step(event: Event, clock: time.Step) -> View {
 state value: int = 0
 let update = Update(value, event, clock)
 next value = update.value
 output update
}
effect fn main() { Counter() }
'''


class NativeEventLanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = native_service()
        cls.addClassCleanup(cls.compiler.close)

    def request(self, operation, *, source=None, **kwargs):
        if source is not None:
            path = Path(__file__).resolve().parents[1] / 'pixellang/stdlib/time.pxl'
            kwargs['files'] = {'main.pxl': source, **{'std/'+p.name:p.read_text() for p in path.parent.glob('*.pxl')}}
        response = self.compiler.request(dict(version=1, operation=operation, **kwargs))
        self.assertEqual(response['status'], 'ok', response)
        return response['result']

    def test_registered_flow_uses_native_state_and_explicit_clock(self):
        runtime = NativeEventRuntime(VM(self.request('compile', source=APPLICATION)['bytecode']))
        self.assertEqual(runtime.step(), dict(value=0, tick=0, elapsed=0, delta=0))
        runtime.enqueue(dict(variant='Add', fields={'value': 7}), tick=1)
        self.assertEqual(runtime.step(), dict(value=0, tick=1, elapsed=100000000, delta=100000000))
        self.assertEqual(runtime.step(), dict(value=7, tick=1, elapsed=100000000, delta=0))
        final = runtime.vm.checkpoint()
        self.assertEqual(runtime.seek(1)['value'], 0)
        runtime.seek(3)
        self.assertEqual(runtime.vm.checkpoint(), final)

    def test_rejected_event_preserves_native_state_and_queue(self):
        runtime = NativeEventRuntime(VM(self.request('compile', source=APPLICATION)['bytecode']))
        runtime.step()
        runtime.enqueue(dict(variant='Reject', fields={}))
        before = runtime.vm.checkpoint()
        with self.assertRaisesRegex(PixelError, 'Assertion failed'):
            runtime.step()
        self.assertEqual(runtime.vm.checkpoint(), before)
        self.assertEqual((runtime.cursor, len(runtime.pending)), (1, 1))

    def test_plain_png_keeps_application_contract_without_original_type_names(self):
        image = self.request('export-png', source=APPLICATION)['image']
        code = self.request('compile-png', image=image)['bytecode']
        runtime = NativeEventRuntime(VM(code))
        self.assertEqual(runtime.step(), dict(value=0, tick=0, elapsed=0, delta=0))
        recovered = self.request('recover-png', image=image)
        rebuilt = self.request('compile', **recovered)['bytecode']
        self.assertEqual(NativeEventRuntime(VM(rebuilt)).step(), runtime.view())

    def test_application_registration_rejects_type_and_effect_errors(self):
        for source in (
            APPLICATION.replace('Counter() }', 'Counter().run(Event.Tick, 20) }'),
            APPLICATION.replace('effect fn main()', 'fn main()'),
            APPLICATION.replace('clock: time.Step) -> View {\n state', 'clock: int) -> View {\n state'),
            APPLICATION.replace('var value = previous', 'Counter().run(Event.Tick, clock.delta)\n var value = previous'),
        ):
            path = Path(__file__).resolve().parents[1] / 'pixellang/stdlib/time.pxl'
            response = self.compiler.request(dict(version=1, operation='compile',
                files={'main.pxl': source, **{'std/'+p.name:p.read_text() for p in path.parent.glob('*.pxl')}}))
            self.assertEqual(response['status'], 'diagnostics', response)
