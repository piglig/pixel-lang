"""Native loop lowering saves operand boundaries and unwinds lexical exits."""
import re
import json
from pathlib import Path
import unittest

from pixellang.code_image import CodeImage
from pixellang.vm import VM
from tests.native_compiler import compile_native_sources
from tests.selfhost_runtime import SELFHOST_HEAP_LIMITS

ROOT = Path(__file__).resolve().parents[1]
DRIVER = '''import "project.pxl" as project
import "bindings.pxl" as bindings
import "types.pxl" as types
import "checker.pxl" as checker
import "instances.pxl" as instances
import "closures.pxl" as closures
import "ir.pxl" as ir
import "assembler.pxl" as assembler
effect fn main(){
 let checked=checker.Check(types.Analyze(bindings.Bind(project.Build(map[string,string]{"main.pxl":input()},"main.pxl"))))
 print(checked.diagnostics)
 if len(checked.diagnostics)>0{return}
 var planned=closures.Build(instances.Build(checked))
 print(assembler.Assemble(ir.Build(&planned)))
}'''

class NativeLoopUnwindTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files = {'main.pxl': DRIVER}
        pending = re.findall(r'^import "([^"]+)"', DRIVER, re.M)
        while pending:
            name = pending.pop()
            if name in files:
                continue
            files[name] = (ROOT / 'selfhost' / name).read_text()
            pending.extend(re.findall(r'^import "([^"]+)"', files[name], re.M))
        cls.code = CodeImage(compile_native_sources(files))

    def execute(self, source, expected, *, loops=True):
        result = VM(self.code, input_text=source, max_steps=50_000_000, max_depth=1024,
                    max_output_chars=32_000_000, **SELFHOST_HEAP_LIMITS).run()
        self.assertEqual(result[0], [])
        self.assertEqual(result[1]['diagnostics'], [])
        code = result[1]['bytecode']
        # VM display snapshots render native json fields as JSON text.
        code['constants'] = [json.loads(value) for value in code['constants']]
        for function in code['functions'].values():
            for instruction in function['instructions']:
                instruction['arg'] = json.loads(instruction['arg'])
        ops = [i['op'] for f in code['functions'].values() for i in f['instructions']]
        if loops:
            self.assertIn('STACK_DEPTH', ops)
            self.assertIn('UNWIND', ops)
        vm = VM(code)
        self.assertEqual(vm.run(), expected)
        self.assertEqual(vm.value_accesses.entries, {})

    def test_for_and_while_break_continue_keep_results(self):
        self.execute('''effect fn main(){
 var total=0
 for n in [1,2,3,4]{if n==2{continue};if n==4{break};total+=n}
 var n=0
 while n<4{n+=1;if n==2{continue};if n==4{break};total+=n}
 print(total)
}''', [8])

    def test_loop_inside_value_block_preserves_pending_inout_argument(self):
        self.execute('''fn add(value:inout int, amount:int)->int{value+=amount;return value}
effect fn main(){
 var value=10
 print(add(&value,match 1 {_=>{var amount=0;for n in [1,2,3,4]{if n==2{continue};if n==4{break};amount+=n};amount}}))
 print(value)
}''', [14,14])

    def test_nested_loop_boundaries_do_not_drop_outer_expression_operand(self):
        self.execute('''effect fn main(){
 print(100+match 1 {_=>{var result=0;for n in [1,2]{while true{result+=n;break};if n==1{continue};break};result}})
}''', [103])





    def test_nested_lambda_cannot_jump_to_enclosing_function_loop(self):
        source='fn main(){while true{let f=fn(){(match 1 {_=>{break}})};break}}'
        result=VM(self.code,input_text=source,max_steps=30_000_000,max_depth=1024,
                  **SELFHOST_HEAP_LIMITS).run()
        self.assertTrue(any(d['message']=='Loop control outside a loop' for d in result[0]),result)




if __name__ == '__main__':
    unittest.main()
