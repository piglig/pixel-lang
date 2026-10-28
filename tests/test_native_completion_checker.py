"""Native checker uses completion paths for unreachable-statement diagnostics."""
from pathlib import Path
import re
import unittest

from pixellang.code_image import CodeImage
from pixellang.vm import VM
from tests.native_compiler import compile_native_sources

ROOT = Path(__file__).resolve().parents[1]

class NativeCompletionCheckerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files = {}
        def add(name):
            if name in files:
                return
            files[name] = (ROOT / 'selfhost' / name).read_text()
            for child in re.findall(r'^import "([^"]+)"', files[name], re.M):
                add(child)
        for name in ('project.pxl', 'bindings.pxl', 'types.pxl', 'checker.pxl'):
            add(name)
        files['main.pxl'] = '''import "project.pxl" as project
import "bindings.pxl" as bindings
import "types.pxl" as types
import "checker.pxl" as checker
effect fn main(){
 let result=checker.Check(types.Analyze(bindings.Bind(project.Build(map[string,string]{"main.pxl":input()},"main.pxl"))))
 print(result.diagnostics)
}'''
        cls.code = CodeImage(compile_native_sources(files))

    def diagnostics(self, source):
        return VM(self.code, input_text=source, max_steps=30_000_000, max_depth=1024).run()[0]

    def test_if_mixed_loop_exits_make_following_statement_unreachable(self):
        source = 'fn main(){while true {if true {break}else{continue};let unreachable=1}}'
        errors = self.diagnostics(source)
        self.assertEqual([d['message'] for d in errors], ['Unreachable statement'])
        self.assertEqual(source[errors[0]['start']:errors[0]['end']], 'let unreachable=1')

    def test_exhaustive_match_mixed_exits_terminate_the_loop_body(self):
        source = 'fn main(){while true {let value:Option[int]=.Some(1);match value {Option.Some(n)=>{break},Option.None=>{continue}};let unreachable=1}}'
        self.assertEqual([d['message'] for d in self.diagnostics(source)], ['Unreachable statement'])

    def test_value_block_local_loop_obeys_same_reachability_rules(self):
        source = 'fn main(){let value=match 1 {_=>{while true {if true {break}else{continue};let unreachable=1};2}}}'
        self.assertEqual([d['message'] for d in self.diagnostics(source)], ['Unreachable statement'])

    def test_nested_loop_jumps_do_not_terminate_outer_block(self):
        self.assertEqual(self.diagnostics('fn main(){while true {while true {break};let reachable=1;break}}'), [])

    def test_partial_branch_and_nested_callable_keep_normal_path(self):
        self.assertEqual(self.diagnostics('fn main(){while true {if false {continue};let f=fn(){return};f();break}}'), [])

if __name__ == '__main__':
    unittest.main()
