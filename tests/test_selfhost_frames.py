import json
import unittest
import time
from pathlib import Path
from unittest.mock import patch

from tests.native_compiler import compile_native_sources, native_service
from pixellang.compiler_service import CompilerService
from pixellang.vm import VM
from tests.selfhost_runtime import (SELFHOST_HEAP_LIMITS, SELFHOST_MAX_STEPS)

ROOT = Path(__file__).resolve().parents[1]


class SelfHostedFrameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = {p.name: p.read_text() for p in (ROOT / 'selfhost').glob('*.pxl')}
        cls.bytecode = compile_native_sources(cls.files)
        cls.service = native_service(cls.bytecode)
        cls.addClassCleanup(cls.service.close)
        cls.native = cls.service.native

    def plan(self, source, summary=False):
        files = source if isinstance(source, dict) else {'main.pxl': source}
        request = json.dumps({
            'source': 'main.pxl', 'text': '',
            'operation': 'frames-summary' if summary else 'frames', 'files': files,
        })
        with patch('pixellang.text.parse_text', side_effect=AssertionError('delegated')):
            result = self.native.execute(request, root=ROOT, steps=SELFHOST_MAX_STEPS,
                output_chars=1_000_000, file_bytes=64_000_000,
                cancelled=lambda: False, deadline=time.monotonic()+120)[0]
        self.assertEqual(result['diagnostics'], [])
        return result

    def names(self, result, frame):
        return [result['symbols'][s['symbol']]['name'] for s in frame['slots']]

    def test_parameter_order_and_shadowed_bindings(self):
        result = self.plan('effect fn work(a: int, b: string) { let x = a; if true { let x = b; print(x) }; print(x) }\neffect fn main() { work(1, "x") }')
        frame = next(f for f in result['functions'] if f['parameters'] == 2)
        self.assertEqual(self.names(result, frame), ['a', 'b', 'x', 'x'])
        self.assertEqual([s['kind'] for s in frame['slots']], ['parameter', 'parameter', 'local', 'local'])
        self.assertEqual(len({s['symbol'] for s in frame['slots']}), 4)
        self.assertEqual(result['entry'], 0)

    def test_parameters_then_captures_then_locals(self):
        result = self.plan('effect fn main() { var base = 1; let f = fn(x: int) -> int { let sum = base + x; return sum }; print(f(2)) }')
        frame = next(f for f in result['functions'] if f['closure'] >= 0)
        self.assertEqual(self.names(result, frame), ['x', 'base', 'sum'])
        self.assertEqual([s['kind'] for s in frame['slots']], ['parameter', 'capture', 'local'])
        self.assertEqual((frame['parameters'], frame['captures']), (1, 1))
        self.assertFalse(frame['slots'][1]['mutable'])
        self.assertTrue(result['symbols'][frame['slots'][1]['symbol']]['mutable'])
        main = result['functions'][result['entry']]
        self.assertEqual(self.names(result, main), ['base', 'f'])
        self.assertEqual(main['slots'][0]['symbol'], frame['slots'][1]['symbol'])

    def test_generic_local_and_closure_parameter_substitution(self):
        result = self.plan('fn make[T](value: T) -> fn(T)->T { let copy = value; return fn(x: T) -> T { let y = x; return copy } }\neffect fn main() { print(make(1)(2)); print(make("x")("y")) }')
        closures = [f for f in result['functions'] if f['closure'] >= 0]
        self.assertEqual(len(closures), 2)
        for frame, expected in zip(closures, ['int', 'string']):
            self.assertEqual(self.names(result, frame), ['x', 'copy', 'y'])
            self.assertTrue(all(result['types'][s['typeId']]['name'] == expected for s in frame['slots']))

    def test_nested_closures_keep_transitive_source_identity(self):
        result = self.plan('effect fn main() { var value = 1; let outer = fn() -> fn()->int { let local = 2; return fn() = value + local }; print(outer()()) }')
        closures = [f for f in result['functions'] if f['closure'] >= 0]
        self.assertEqual(len(closures), 2)
        value_slots = [next(s for s in f['slots'] if result['symbols'][s['symbol']]['name'] == 'value') for f in closures]
        self.assertEqual(value_slots[0]['symbol'], value_slots[1]['symbol'])
        self.assertTrue(all(s['kind'] == 'capture' for s in value_slots))
        local_slots = [next(s for s in f['slots'] if result['symbols'][s['symbol']]['name'] == 'local') for f in closures]
        self.assertEqual(local_slots[0]['symbol'], local_slots[1]['symbol'])
        self.assertEqual({s['kind'] for s in local_slots}, {'capture', 'local'})

    def test_loop_and_unused_result_bindings_receive_slots(self):
        result = self.plan('effect fn main() { for index, value in [1, 2] { print(value) }; match jsonDecode::[int]("invalid") { .Ok(number) => {}, .Err(err) => {} } }')
        main = result['functions'][result['entry']]
        self.assertEqual(self.names(result, main), ['index', 'value', 'number', 'err'])
        self.assertEqual([result['types'][s['typeId']]['name'] for s in main['slots']], ['int', 'int', 'int', 'Error'])

    def test_local_value_writes_follow_binding_permissions(self):
        result = self.plan('fn main() { let original = [1]; var changed = original; changed.append(3); changed = [4] }')
        frame = result['functions'][result['entry']]
        slots = {result['symbols'][s['symbol']]['name']: s for s in frame['slots']}
        self.assertFalse(slots['original']['mutable'])
        self.assertTrue(slots['changed']['mutable'])
        self.assertEqual(slots['original']['typeId'], slots['changed']['typeId'])
        self.assertEqual(result['types'][slots['changed']['typeId']]['kind'], 'array')

    def test_compiler_frame_layout(self):
        result = self.plan(self.files, summary=True)
        self.assertGreater(result['functions'], 100)
        self.assertGreater(result['slots'], 500)

    def test_native_matches_reference_layout(self):
        source = 'effect fn main(){var n=1;let f=fn(x:int)=n+x;print(f(2))}'
        request = json.dumps(dict(source='main.pxl', text='', operation='frames',
                                  files={'main.pxl': source}))
        expected = VM(self.bytecode, **SELFHOST_HEAP_LIMITS, input_text=request,
                      max_steps=SELFHOST_MAX_STEPS, max_depth=1024).run()[0]
        self.assertEqual(self.plan(source), expected)
