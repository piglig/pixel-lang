"""Access modes belong to parameters and explicit call arguments, not values."""
import json
from pathlib import Path
import unittest
from pixellang.vm import VM
from tests.native_compiler import compile_native_sources

ROOT = Path(__file__).resolve().parents[1]

class InoutParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files = {name: (ROOT/'selfhost'/name).read_text()
                 for name in ('model.pxl', 'lexer.pxl', 'parser.pxl', 'source_format.pxl')}
        files['main.pxl'] = '''import "parser.pxl" as parser
import "source_format.pxl" as format
effect fn main() { let source=input(); print(jsonFrom(parser.Parse("main.pxl",source))); print(jsonFrom(format.Format("main.pxl",source))) }
'''
        cls.code = compile_native_sources(files)

    def parse(self, source):
        result = VM(self.code, input_text=source, max_steps=4000000).run()
        return json.loads(result[0]), json.loads(result[1])

    def test_named_generic_anonymous_and_function_type_parameters(self):
        source = '''fn change[T](value: inout T, replacement: T) { value = replacement }
fn apply(operation: fn(inout [int]), values: inout [int]) { operation(&values) }
fn main() { var values=[1]; let f=fn(x: inout [int]) { x[0]=2 }; apply(f,&values) }
'''
        parsed, formatted = self.parse(source)
        self.assertEqual(parsed['diagnostics'], [])
        self.assertEqual(sum(n['kind']=='type_inout' for n in parsed['nodes']), 4)
        self.assertEqual(sum(n['kind']=='inout_argument' for n in parsed['nodes']), 2)
        self.assertIn('inout [int]', formatted['text'])
        self.assertIn('&values', formatted['text'])
        _, again = self.parse(formatted['text'])
        self.assertEqual(again['text'], formatted['text'])

    def test_address_path_and_span_are_preserved(self):
        source = 'fn main() { change(&rows[index()].value) }'
        parsed, _ = self.parse(source)
        self.assertEqual(parsed['diagnostics'], [])
        argument, = [n for n in parsed['nodes'] if n['kind']=='inout_argument']
        self.assertEqual(source[argument['start']:argument['end']], '&rows[index()].value')
        self.assertEqual(parsed['nodes'][argument['children'][0]]['kind'], 'field')

    def test_mut_is_not_a_type_permission_modifier(self):
        for source in (
            'record Box { value: mut [int] }',
            'fn edit(value: mut Box) {}',
            'fn edit(value: inout mut [int]) {}',
            'fn make() -> mut [int] { return [] }',
            'fn main() { var value: mut [int] = [] }',
            'fn main() { let f=fn(value: mut [int]) {} }',
        ):
            with self.subTest(source=source):
                parsed, _ = self.parse(source)
                # A bare nominal name with generic arguments may still parse;
                # it must never create a permission-wrapper AST.
                self.assertFalse(any(n['kind']=='type_mutable' for n in parsed['nodes']))
        parsed, _ = self.parse('record mut { value:int }\nfn copy(x:mut)->mut=x')
        self.assertEqual(parsed['diagnostics'], [])

    def test_inout_cannot_be_a_stored_type_or_free_reference_expression(self):
        for source in [
            'fn main() { let address=&x }',
            'record Box { value: inout int }',
            'fn bad() -> inout int { return x }',
            'fn bad(x: [inout int]) {}',
            'fn bad(x: inout inout int) {}',
            'fn main() { change(&make()) }',
            'fn main() { change(&x + y) }',
            'fn main() { change(&1) }',
        ]:
            with self.subTest(source=source):
                parsed, _ = self.parse(source)
                self.assertTrue(parsed['diagnostics'])
