import json
import unittest
from pathlib import Path
from unittest.mock import patch

from pixellang.bootstrap import read_bytecode_stream
from pixellang.model import PixelError
from pixellang.project import compile_project
from pixellang.vm import VM
from tests.selfhost_runtime import SELFHOST_HEAP_LIMITS


class BytecodeStreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.compiler = compile_project({p.name: p.read_text() for p in (root / 'selfhost').glob('*.pxl')})[1].bytecode

    def compile(self, source, operation):
        vm = VM(self.compiler, **SELFHOST_HEAP_LIMITS, max_steps=100_000_000,
                max_depth=1024, input_text=json.dumps(dict(source='main.pxl', text='',
                operation=operation, files={'main.pxl': source})))
        with patch('pixellang.text.parse_text', side_effect=AssertionError('delegated')), patch(
            'pixellang.backend.assemble', side_effect=AssertionError('delegated')):
            return vm.run()

    def test_round_trip_preserves_complete_bytecode(self):
        source = 'record Box[T] { value: T }\nenum Choice { Yes(value: int), No }\nfn main() { let box = Box[int]{value: 3}; let f = fn() = box.value; print(f()); let value = Choice.Yes(7); print(match value { Choice.Yes(number) => number, Choice.No => 0 }) }'
        direct = json.loads(self.compile(source, 'bytecode')[0])
        events = self.compile(source, 'bytecode-stream')
        self.assertEqual(read_bytecode_stream(events), direct)
        self.assertEqual(VM(direct['bytecode']).run(), [3, 7])
        for malformed in (events[:-1], events + [events[-1]], [events[0], events[0]] + events[1:]):
            with self.assertRaises(ValueError):
                read_bytecode_stream(malformed)
        altered = list(events)
        end = json.loads(altered[-1])
        end['instructions'] += 1
        altered[-1] = json.dumps(end)
        with self.assertRaisesRegex(ValueError, 'counts'):
            read_bytecode_stream(altered)

    def test_diagnostics_are_not_executable(self):
        result = read_bytecode_stream(self.compile('fn main() { print(missing) }', 'bytecode-stream'))
        self.assertTrue(result['diagnostics'])
        self.assertEqual(result['bytecode']['entry'], [])
        self.assertEqual(result['bytecode']['functions'], {})

    def test_emitter_delivers_more_than_single_json_limit(self):
        output = json.loads(self.compile('fn main() { print("x") }', 'bytecode')[0])
        output['bytecode']['constants'][0]['value'] = 'x' * 600000
        root = Path(__file__).resolve().parents[1]
        files = {p.name: p.read_text() for p in (root / 'selfhost').glob('*.pxl')}
        files['main.pxl'] = '''import "assembler.pxl" as assembler
import "bytecode_stream.pxl" as stream
fn main() {
    let output = jsonDecode[assembler.Output](input())
    append(output.bytecode.constants, output.bytecode.constants[0])
    stream.Emit(output)
}'''
        driver = compile_project(files)[1].bytecode
        events = VM(driver, input_text=json.dumps(output), max_steps=1_000_000,
                    max_output_chars=2_000_000).run()
        self.assertGreater(sum(map(len, events)), 1_000_000)
        self.assertTrue(all(len(event) < 1_000_000 for event in events))
        restored = read_bytecode_stream(events)['bytecode']
        self.assertEqual(len(restored['constants']), len(output['bytecode']['constants']) + 1)
        self.assertEqual(restored['constants'][0], restored['constants'][-1])
        self.assertEqual(VM(restored).run(), ['x' * 600000])

    def test_explicit_output_budget_retains_default_limit(self):
        code = compile_project({'main.pxl': 'fn main() { print(input()); print(input()) }'})[1].bytecode
        with self.assertRaisesRegex(PixelError, 'Output budget'):
            VM(code, input_text='x' * 600000).run()
        result = VM(code, input_text='x' * 600000, max_output_chars=1_200_000).run()
        self.assertEqual([len(item) for item in result], [600000, 600000])
        for value in (0, -1, True, 1.5):
            with self.assertRaisesRegex(PixelError, 'Output budget must be positive'):
                VM(code, max_output_chars=value)
