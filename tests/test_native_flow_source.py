"""Expression-flow ASTs round-trip through native source emission."""
import json
from pathlib import Path
import unittest

from pixellang.code_image import CodeImage
from pixellang.vm import VM
from tests.native_compiler import compile_native_sources

ROOT=Path(__file__).resolve().parents[1]
DRIVER='''import "project.pxl" as project
import "source_writer.pxl" as writer
import "parser.pxl" as parser
effect fn main(){
 let source=input()
 let built=project.Build(map[string,string]{"main.pxl":source},"main.pxl")
 let written=writer.Build(built,map[string,string]{},map[string,string]{})
 print(written)
 if len(written.diagnostics)==0 {print(parser.Parse("main.pxl",written.files["main.pxl"]))}
}'''


class NativeFlowSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files={name:(ROOT/'selfhost'/name).read_text() for name in
               ('model.pxl','lexer.pxl','parser.pxl','project.pxl','source_writer.pxl','failures.pxl')}
        cls.code=CodeImage(compile_native_sources({**files,'main.pxl':DRIVER}))

    def rewrite(self,source):
        return VM(self.code,input_text=source,max_steps=20_000_000).run()

    def test_state_expressions_and_updates_round_trip_idempotently(self):
        source='''record R {value:int}
flow F(initial:R) step(delta:int)->R {
 state current:R=initial
 let value=current.value+delta
 next current=current with {value}
 output current
}'''
        written,parsed=self.rewrite(source)
        self.assertEqual(written['diagnostics'],[])
        self.assertEqual(parsed['diagnostics'],[])
        text=written['files']['main.pxl']
        self.assertIn('flow F(initial: R) step(delta: int)',text)
        self.assertIn('state current: R = initial',text)
        self.assertIn('let value =',text)
        self.assertEqual(self.rewrite(text)[0],written)

    def test_inferred_state_and_explicit_child_declarations_keep_identity(self):
        written,parsed=self.rewrite('''flow Parent(initial:int) step(x:int)->int {
 state seen=0
 instance child=Counter(initial)
 let result:int=child.step(x)
 next seen=seen+1
 output result
}
flow Counter(initial:int) step(x:int)->int {state value=initial;next value=x;output value}''')
        self.assertEqual(written['diagnostics'],[])
        self.assertEqual(parsed['diagnostics'],[])
        text=written['files']['main.pxl']
        self.assertIn('state seen = 0',text)
        self.assertIn('instance child = Counter(initial)',text)
        self.assertIn('child.step(x)',text)
        self.assertEqual(sum(n['kind']=='flow_instance' for n in parsed['nodes']),1)
        self.assertEqual(self.rewrite(text)[0],written)

    def test_context_variants_and_match_outputs_remain_expressions(self):
        written,parsed=self.rewrite('''enum S {Ready,Done(value:int)}
flow F() step(x:S)->int {state old:S=.Ready;next old=x;output match old {.Ready=>0,.Done(value)=>value}}''')
        self.assertEqual(written['diagnostics'],[])
        self.assertEqual(parsed['diagnostics'],[])
        self.assertIn('flow F() step(x: S)',written['files']['main.pxl'])
        self.assertEqual(self.rewrite(written['files']['main.pxl'])[0],written)


if __name__=='__main__':unittest.main()
