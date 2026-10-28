"""Native value-block scopes and source recovery preserve tail semantics."""
from pathlib import Path
import unittest

from pixellang.code_image import CodeImage
from pixellang.vm import VM
from tests.native_compiler import compile_native_sources

ROOT = Path(__file__).resolve().parents[1]
DRIVER = '''import "project.pxl" as project
import "bindings.pxl" as bindings
import "source_writer.pxl" as writer
effect fn main(){
 let built=project.Build(map[string,string]{"main.pxl":input()},"main.pxl")
 print(bindings.Bind(built))
 print(writer.Build(built,map[string,string]{},map[string,string]{}))
}'''


class NativeMatchBlockSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files = {name: (ROOT / 'selfhost' / name).read_text() for name in
                 ('model.pxl', 'lexer.pxl', 'parser.pxl', 'project.pxl',
                  'bindings.pxl', 'source_writer.pxl', 'failures.pxl')}
        cls.code = CodeImage(compile_native_sources({**files, 'main.pxl': DRIVER}))

    def rewrite(self, source):
        return VM(self.code, input_text=source, max_steps=20_000_000,
                  max_output_chars=16_000_000).run()

    def test_tail_and_discard_survive_idempotent_source_recovery(self):
        for body, result_count in [('let x=1; x+2', 1), ('print(1);', 0), ('', 0)]:
            with self.subTest(body=body):
                bound, written = self.rewrite('effect fn main(){match Option::[int].Some(1){_=>{print(0)}};'
                    'let value=match Option::[int].None {_=>{'+body+'}}}')
                self.assertEqual(bound['diagnostics'], [])
                self.assertEqual(written['diagnostics'], [])
                restored, repeated = self.rewrite(written['files']['main.pxl'])
                self.assertEqual(restored['diagnostics'], [])
                self.assertEqual(repeated, written)
                nodes = restored['project']['modules'][0]['syntax']['nodes']
                self.assertEqual(sum(n['kind'] == 'block_result' for n in nodes), result_count)

    def test_arm_local_names_do_not_escape_or_collide_between_arms(self):
        source = '''fn f(value:Option[int])->int = match value {
 Option.Some(n)=>{let local=n;local}, Option.None=>{let local=0;local}
}
effect fn main(){print(local)}'''
        bound, _ = self.rewrite(source)
        self.assertEqual(len(bound['diagnostics']), 1, bound['diagnostics'])
        self.assertIn('Unknown name: local', bound['diagnostics'][0]['message'])
        declarations = [s for s in bound['symbols'] if s['name'] == 'local']
        self.assertEqual(len(declarations), 2)
        self.assertNotEqual(declarations[0]['scope'], declarations[1]['scope'])

    def test_nested_value_blocks_round_trip_without_function_wrappers(self):
        source = 'fn f(a:Option[int],b:Option[int])->int = match a {_=>{let n=1;match b {Option.Some(x)=>{x+n},Option.None=>n}}}'
        bound, written = self.rewrite(source)
        self.assertEqual(bound['diagnostics'], [])
        self.assertEqual(written['diagnostics'], [])
        restored, repeated = self.rewrite(written['files']['main.pxl'])
        self.assertEqual(restored['diagnostics'], [])
        self.assertEqual(repeated, written)
        nodes = restored['project']['modules'][0]['syntax']['nodes']
        self.assertEqual(sum(n['kind'] == 'match' for n in nodes), 2)
        self.assertEqual(sum(n['kind'] == 'fn' for n in nodes), 1)
        self.assertFalse(any(n['kind'] == 'lambda' for n in nodes))


if __name__ == '__main__':
    unittest.main()
