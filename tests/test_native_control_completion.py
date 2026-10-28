"""Native completion analysis tracks statements, local loops and propagation."""
from pathlib import Path
import unittest

from pixellang.code_image import CodeImage
from pixellang.vm import VM
from tests.native_compiler import compile_native_sources

ROOT = Path(__file__).resolve().parents[1]
DRIVER = '''import "parser.pxl" as parser
import "control_flow.pxl" as flow
effect fn main(){
 let parsed=parser.Parse("completion.pxl",input())
 print(parsed.diagnostics)
 if len(parsed.diagnostics)>0{return}
 let function=parsed.nodes[parsed.roots[0]]
 print(flow.Analyze(parsed.nodes,function.children[len(function.children)-1]))
}'''


class NativeControlCompletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files = {name: (ROOT / 'selfhost' / name).read_text() for name in
                 ('model.pxl', 'lexer.pxl', 'parser.pxl', 'control_flow.pxl')}
        cls.code = CodeImage(compile_native_sources({**files, 'main.pxl': DRIVER}))

    def completion(self, body):
        results = VM(self.code, input_text='effect fn f(){'+body+'}', max_steps=20_000_000,
                     max_output_chars=8_000_000).run()
        self.assertEqual(results[0], [])
        return tuple(results[1][key] for key in ('normal', 'returns', 'breaks', 'continues'))

    def test_sequence_cannot_revive_a_terminated_path(self):
        self.assertEqual(self.completion('return;print(1)'), (False, True, False, False))
        self.assertEqual(self.completion('break;return'), (False, False, True, False))

    def test_branch_alternatives_require_both_paths_to_terminate(self):
        self.assertEqual(self.completion('if flag{return}else{return}'), (False, True, False, False))
        self.assertEqual(self.completion('if flag{return}'), (True, True, False, False))

    def test_value_blocks_complete_normally_and_propagation_keeps_a_success_path(self):
        self.assertEqual(self.completion('let x=match value {.Some(n)=>{n},.None=>{0}}'),
                         (True, False, False, False))
        self.assertEqual(self.completion('let x=match value {.Some(n)=>{parseInt(n)?},.None=>0};print(x)'),
                         (True, True, False, False))

    def test_nested_loops_consume_only_their_own_jumps(self):
        self.assertEqual(self.completion('while flag {while other {break};continue}'),
                         (True, False, False, False))
        self.assertEqual(self.completion('for x in values {return x}'), (True, True, False, False))

    def test_value_block_local_loops_consume_their_jumps(self):
        body = 'let x=match value {.Some(n)=>{while flag {if other{break}else{continue}};n},.None=>0}'
        self.assertEqual(self.completion(body), (True, False, False, False))
        self.assertEqual(self.completion('while flag {'+body+'}'), (True, False, False, False))

    def test_lambda_and_named_function_returns_do_not_escape(self):
        self.assertEqual(self.completion('let f=fn(){return};fn nested(){return}'),
                         (True, False, False, False))

    def test_short_circuit_and_propagation_retain_success_path(self):
        self.assertEqual(self.completion('let x=flag && choose()?'),
                         (True, True, False, False))
        self.assertEqual(self.completion('let x=parseInt("1")?'), (True, True, False, False))


if __name__ == '__main__':
    unittest.main()
