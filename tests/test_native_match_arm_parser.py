"""Native match arms accept single expressions in statement position."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from pixellang.code_image import CodeImage
from pixellang.vm import VM
from pixellang.bootstrap import read_bytecode_stream
from pixellang.compiler_service import CompilerService
from pixellang.image_transport import stage_parsed
from tests.native_compiler import compile_native_sources

ROOT = Path(__file__).resolve().parents[1]
DRIVER = '''import "parser.pxl" as parser
effect fn main() { print(parser.Parse("match.pxl", input())) }
'''


class NativeMatchArmParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files = {name: (ROOT / 'selfhost' / name).read_text()
                 for name in ('model.pxl', 'lexer.pxl', 'parser.pxl')}
        cls.image = CodeImage(compile_native_sources({**files, 'main.pxl': DRIVER}))

    def parse(self, source):
        return VM(self.image, input_text=source, max_steps=20_000_000,
                  max_output_chars=16_000_000).run()[0]

    def test_single_call_and_block_share_statement_match(self):
        source = 'effect fn main(){match Option::[int].Some(1) { Option.Some(value) => print(1), Option.None => { print(2) } }}'
        parsed = self.parse(source)
        self.assertEqual(parsed['diagnostics'], [])
        nodes = parsed['nodes']
        match = next(n for n in nodes if n['kind'] == 'match_stmt')
        bodies = [nodes[nodes[i]['children'][-1]] for i in match['children'][1:]]
        self.assertEqual([n['kind'] for n in bodies], ['expression_stmt', 'block'])
        self.assertEqual(source[bodies[0]['start']:bodies[0]['end']], 'print(1)')
        self.assertFalse(any(n['kind'] == 'lambda' for n in nodes))

    def test_multiline_guarded_product_arms_preserve_patterns(self):
        parsed = self.parse('''effect fn main(){match (true, false) {
 (a, b) if a =>
  print(b),
 (_, _) => print(false)
}}''')
        self.assertEqual(parsed['diagnostics'], [])
        nodes = parsed['nodes']
        arms = [n for n in nodes if n['kind'] == 'arm']
        self.assertEqual([len(n['children']) for n in arms], [3, 2])
        self.assertEqual([len(nodes[n['children'][0]]['children']) for n in arms], [2, 2])

    def test_expression_match_still_produces_values(self):
        parsed = self.parse('fn choose(b:Option[int])->int = match b { Option.Some(value) =>\n 1, Option.None => 2 }')
        self.assertEqual(parsed['diagnostics'], [])
        nodes = parsed['nodes']
        self.assertTrue(any(n['kind'] == 'match' for n in nodes))
        self.assertEqual([nodes[n['children'][-1]]['kind'] for n in nodes if n['kind'] == 'arm'],
                         ['literal_int', 'literal_int'])

    def test_statement_only_forms_require_a_block(self):
        for body in ('return 1', 'let x = 1', 'value = 1'):
            with self.subTest(body=body):
                parsed = self.parse('fn f(b:Option[int])->int {var value=0;match b {Option.Some(n)=>'+body+',Option.None=>{return 0}}}')
                self.assertTrue(parsed['diagnostics'])

    def test_value_block_retains_statements_and_native_tail(self):
        source = '''fn choose(value: Option[int])->int = match value {
 Option.Some(n) => { let doubled=n*2; doubled+1 },
 Option.None => { 0 }
}'''
        parsed = self.parse(source)
        self.assertEqual(parsed['diagnostics'], [])
        nodes = parsed['nodes']
        blocks = [n for n in nodes if n['kind'] == 'value_block']
        self.assertEqual([[nodes[i]['kind'] for i in n['children']] for n in blocks],
                         [['let', 'block_result'], ['block_result']])
        tail = nodes[blocks[0]['children'][-1]]
        self.assertEqual(source[tail['start']:tail['end']], 'doubled+1')
        self.assertFalse(any(n['kind'] == 'lambda' for n in nodes))

    def test_value_block_semicolon_discards_tail_but_newline_does_not(self):
        for suffix, kind in [('\n', 'block_result'), (';', 'expression_stmt'), ('\n;\n', 'expression_stmt')]:
            with self.subTest(suffix=suffix):
                parsed = self.parse('fn f(value:Option[int]) = match value { _ => { print(1)'+suffix+'} }')
                self.assertEqual(parsed['diagnostics'], [])
                nodes = parsed['nodes']
                block = next(n for n in nodes if n['kind'] == 'value_block')
                self.assertEqual(nodes[block['children'][-1]]['kind'], kind)

    def test_nested_match_is_a_value_block_tail(self):
        parsed = self.parse('fn f(a:Option[int],b:Option[int])->int = match a {_=>{match b {Option.Some(n)=>{n},Option.None=>0}}}')
        self.assertEqual(parsed['diagnostics'], [])
        nodes = parsed['nodes']
        matches = [n for n in nodes if n['kind'] == 'match']
        self.assertEqual(len(matches), 2)
        self.assertFalse(any(n['kind'] == 'match_stmt' for n in nodes))

    def compile_native_ast(self, source):
        parsed = self.parse(source)
        self.assertEqual(parsed['diagnostics'], [])
        # This stage verifies the new native parser with the installed native
        # backend. It does not claim that the bundled text frontend has changed.
        service = CompilerService(json.loads((ROOT / 'pixellang/artifacts/compiler.json').read_bytes()))
        with TemporaryDirectory(prefix='pixel-match-arms-') as directory:
            root = Path(directory)
            path = stage_parsed(root, '0', dict(syntax=parsed, entries=[], diagnostics=[]))
            events = service._execute(dict(operation='bytecode-stream', source='main.pxl', text='',
                files={'main.pxl': source}, parsedFiles={'main.pxl': path}),
                root, lambda: False, time.monotonic() + 60)
            return read_bytecode_stream(events)

    def test_short_statement_arm_executes_only_the_selected_branch(self):
        result = self.compile_native_ast('''effect fn main() {
 for value in [Option::[int].Some(1), Option::[int].None] {
  match value { Option.Some(n) => print(n), Option.None => { print(2) } }
 }
}''')
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(VM(result['bytecode']).run(), [1, 2])

    def test_short_arm_preserves_effect_and_must_use_checks(self):
        for source, message in (
            ('fn f(b:Option[int]){match b {Option.Some(n)=>print(n),Option.None=>print(2)}}\neffect fn main(){}', 'effect'),
            ('effect fn main(){match Option::[int].Some(1) {Option.Some(n)=>parseInt("1"),Option.None=>parseInt("2")}}', 'Must-use'),
        ):
            with self.subTest(message=message):
                result = self.compile_native_ast(source)
                self.assertTrue(any(message.lower() in str(item).lower() for item in result['diagnostics']), result)


if __name__ == '__main__':
    unittest.main()
