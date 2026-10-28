"""Logical time executes through the self-hosted compiler, including PNG."""
from tests.native_compiler import native_service
from contextlib import ExitStack
from pathlib import Path
import unittest
from unittest.mock import patch

from pixellang.bootstrap_image import HOST_STAGES
from pixellang.vm import VM


class NativeTimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = native_service()
        cls.addClassCleanup(cls.compiler.close)
        cls.library = {'std/' + p.name: p.read_text() for p in
                       (Path(__file__).resolve().parents[1] / 'pixellang/stdlib').glob('*.pxl')}

    def request(self, operation, **payload):
        if 'files' in payload:
            payload['files'] = {**self.library, **payload['files']}
        with ExitStack() as stack:
            for target in HOST_STAGES:
                stack.enter_context(patch(target, side_effect=AssertionError('Host frontend used')))
            result = self.compiler.request(dict(version=1, operation=operation, **payload))
        self.assertEqual(result['status'], 'ok', result)
        return result['result']

    def execute(self, body):
        source = 'import "std/time.pxl" as time\n' + body
        code = self.request('compile', files={'main.pxl': source})['bytecode']
        return VM(code).run()

    def test_integer_clock_has_exact_endpoints_without_accumulated_drift(self):
        self.assertEqual(self.execute('''record Endpoints {start:int, end:int, seconds:float64}
effect fn main() {
 match endpoints() {.Ok(values)=>{print(values.start); print(values.end); print(values.seconds)},.Err(error)=>{print(error.code)}}
}
fn endpoints()->Result[Endpoints,Error] {
 let interval = time.Milliseconds(20)?
 let start = time.At(0, interval)?
 let end = time.At(50000, interval)?
 return .Ok(Endpoints{start:start.delta.nanoseconds, end:end.elapsed.nanoseconds, seconds:time.InSeconds(end.elapsed)})
}'''), [0, 1000000000000, 1000.0])

    def test_invalid_time_and_overflow_are_error_values(self):
        self.assertEqual(self.execute('''effect fn main() {
 match time.At(-1, time.Nanoseconds(1)) {.Ok(value)=>{print("unexpected")},.Err(error)=>{print(error.code)}}
 match time.At(1, time.Nanoseconds(0)) {.Ok(value)=>{print("unexpected")},.Err(error)=>{print(error.code)}}
 match time.At(9223372036854775807, time.Nanoseconds(2)) {.Ok(value)=>{print("unexpected")},.Err(error)=>{print(error.code)}}
}'''), ['time.invalid_index', 'time.invalid_interval', 'time.elapsed_range'])

    def test_time_library_survives_native_png_recovery(self):
        source = '''import "std/time.pxl" as time
effect fn main() {
 match elapsed() {.Ok(value)=>{print(value)},.Err(error)=>{print(error.code)}}
}
fn elapsed()->Result[int,Error] {
 let clock = time.At(25, time.Milliseconds(20)?)?
 return .Ok(clock.elapsed.nanoseconds)
}'''
        image = self.request('export-png', files={'main.pxl': source})['image']
        delivered = self.request('compile-png', image=image)['bytecode']
        self.assertEqual(VM(delivered).run(), [500000000])
        restored = self.request('recover-png', image=image)
        self.assertEqual(len(restored['files']), 3)
        # Plain semantic PNGs need neither original names nor installed libraries.
        rebuilt_response = self.compiler.request(dict(version=1, operation='compile', **restored))
        self.assertEqual(rebuilt_response['status'], 'ok', rebuilt_response)
        rebuilt = rebuilt_response['result']['bytecode']
        self.assertEqual(VM(rebuilt).run(), [500000000])
