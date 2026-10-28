import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.native_compiler import compile_native_sources
from pixellang.bootstrap import read_bytecode_stream
from pixellang.model import PixelError
from pixellang.vm import VM
from tests.selfhost_runtime import SELFHOST_HEAP_LIMITS


class BytecodeStreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.compiler = compile_native_sources({p.name: p.read_text() for p in (root / 'selfhost').glob('*.pxl')})

    def compile(self, source, operation):
        vm = VM(self.compiler, **SELFHOST_HEAP_LIMITS, max_steps=100_000_000,
                max_depth=1024, input_text=json.dumps(dict(source='main.pxl', text='',
                operation=operation, files={'main.pxl': source})))
        with patch('pixellang.text.parse_text', side_effect=AssertionError('delegated')):
            return vm.run()

    def test_round_trip_preserves_complete_bytecode(self):
        source = 'record Box[T] { value: T }\nenum Choice { Yes(value: int), No }\neffect fn main() { let box = Box::[int]{value: 3}; let f = fn() = box.value; print(f()); let value = Choice.Yes(7); print(match value { Choice.Yes(number) => number, Choice.No => 0 }) }'
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
        result = read_bytecode_stream(self.compile('effect fn main() { print(missing) }', 'bytecode-stream'))
        self.assertTrue(result['diagnostics'])
        self.assertEqual(result['bytecode']['entry'], [])
        self.assertEqual(result['bytecode']['functions'], {})

    def test_shared_spans_and_invalid_span_references(self):
        events=self.compile('effect fn main(){print(1+2);print(3+4)}','bytecode-stream')
        result=read_bytecode_stream(events)
        instructions=[i for f in result['bytecode']['functions'].values() for i in f['instructions']]
        self.assertLess(len({id(i['span']) for i in instructions}),len(instructions))
        position=next(i for i,e in enumerate(events) if json.loads(e)['kind']=='instruction')
        for reference in (-1,True,1000000,'0'):
            malformed=list(events)
            event=json.loads(malformed[position]);event['value']['span']=reference
            malformed[position]=json.dumps(event)
            with self.subTest(reference=reference),self.assertRaisesRegex(ValueError,'span'):
                read_bytecode_stream(malformed)
        span_position=next(i for i,e in enumerate(events) if json.loads(e)['kind']=='span')
        malformed=list(events);event=json.loads(malformed[span_position]);event['name']='2'
        malformed[span_position]=json.dumps(event)
        with self.assertRaisesRegex(ValueError,'Spans'):
            read_bytecode_stream(malformed)

    def test_emitter_delivers_more_than_single_json_limit(self):
        output = json.loads(self.compile('effect fn main() { print("x") }', 'bytecode')[0])
        output['bytecode']['constants'][0]['value'] = 'x' * 600000
        root = Path(__file__).resolve().parents[1]
        files = {p.name: p.read_text() for p in (root / 'selfhost').glob('*.pxl')}
        files['main.pxl'] = '''import "assembler.pxl" as assembler
import "bytecode_stream.pxl" as stream
effect fn main() {
    match jsonDecode::[assembler.Output](input()) {
        .Ok(original) => {
            var output = original
            output.bytecode.constants.append(output.bytecode.constants[0])
            stream.Emit(output)
        },
        .Err(error) => { print(error.message) }
    }
}'''
        driver = compile_native_sources(files)
        events = VM(driver, input_text=json.dumps(output), max_steps=1_000_000,
                    max_output_chars=2_000_000).run()
        self.assertGreater(sum(map(len, events)), 1_000_000)
        self.assertTrue(all(len(event) < 1_000_000 for event in events))
        restored = read_bytecode_stream(events)['bytecode']
        self.assertEqual(len(restored['constants']), len(output['bytecode']['constants']) + 1)
        self.assertEqual(restored['constants'][0], restored['constants'][-1])
        self.assertEqual(VM(restored).run(), ['x' * 600000])

    def test_explicit_output_budget_retains_default_limit(self):
        code = read_bytecode_stream(self.compile('effect fn main() { print(input()); print(input()) }', 'bytecode-stream'))['bytecode']
        with self.assertRaisesRegex(PixelError, 'Output budget'):
            VM(code, input_text='x' * 600000).run()
        result = VM(code, input_text='x' * 600000, max_output_chars=1_200_000).run()
        self.assertEqual([len(item) for item in result], [600000, 600000])
        for value in (0, -1, True, 1.5):
            with self.assertRaisesRegex(PixelError, 'Output budget must be positive'):
                VM(code, max_output_chars=value)
