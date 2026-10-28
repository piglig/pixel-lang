"""Native inout call contracts preserve access mode and prohibit overlapping writes."""
import json
from pathlib import Path
import re
import unittest
from pixellang.vm import VM
from tests.native_compiler import compile_native_sources

ROOT=Path(__file__).resolve().parents[1]

class InoutCheckerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files={}
        def add(name):
            if name in files: return
            files[name]=(ROOT/'selfhost'/name).read_text()
            for child in re.findall(r'^import "([^"]+)"',files[name],re.M): add(child)
        for name in ('project.pxl','bindings.pxl','types.pxl','checker.pxl'): add(name)
        files['main.pxl']='''import "project.pxl" as project
import "bindings.pxl" as bindings
import "types.pxl" as types
import "checker.pxl" as checker
effect fn main() {
 let result=checker.Check(types.Analyze(bindings.Bind(project.Build(map[string,string]{"main.pxl":input()},"main.pxl"))))
 print(jsonFrom(result.diagnostics))
}
'''
        cls.code=compile_native_sources(files)

    def check(self, source):
        return json.loads(VM(self.code,input_text=source,max_steps=20000000).run()[0])

    def test_generic_and_higher_order_inout_calls(self):
        source='''fn replace[T](value: inout T, next: T) { value=next }
fn edit(values: inout [int]) { values.append(2); values[0]=3 }
fn apply(values: inout [int], action: fn(inout [int])) { action(&values) }
fn main() {
 var n=1
 replace(&n,2)
 var values=[1]
 apply(&values,edit)
 apply(&values,fn(items: inout [int]) { items.append(4) })
}
'''
        self.assertEqual(self.check(source), [])

    def test_missing_extra_markers_and_readonly_arguments_are_rejected(self):
        prefix='fn edit(x: inout int) { x=2 }\nfn read(x: int) {}\n'
        for body, diagnostic in [
            ('var x=1; edit(x)', 'explicit & access marker'),
            ('var x=1; read(&x)', 'explicit & access marker'),
            ('let x=1; edit(&x)', 'readonly binding'),
            ('var x="wrong"; edit(&x)', 'Type'),
        ]:
            with self.subTest(body=body):
                self.assertIn(diagnostic,str(self.check(prefix+'fn main() { '+body+' }')))

    def test_inout_function_modes_are_invariant(self):
        for source in [
            'fn edit(x: inout int) {}\nfn main() { let f: fn(int)=edit }',
            'fn read(x: int) {}\nfn main() { let f: fn(inout int)=read }',
            'fn main() { let f: fn(inout int)=fn(x) {} }',
        ]:
            with self.subTest(source=source): self.assertTrue(self.check(source))

    def test_same_root_and_nested_argument_writes_are_rejected(self):
        prefix='fn pair(x: inout int,y: inout int) {}\nfn edit(x: inout [int],n: int) {}\n'
        for body in [
            'var x=1; pair(&x,&x)',
            'var x=[1,2]; pair(&x[0],&x[1])',
            'var x=[1]; edit(&x,match x.pop() { Option[int].Some(v)=>v,Option[int].None=>0 })',
        ]:
            with self.subTest(body=body):
                self.assertIn('Overlapping value writes',str(self.check(prefix+'fn main() { '+body+' }')))

    def test_separate_roots_and_sequential_reborrows_are_legal(self):
        self.assertEqual(self.check('''fn pair(x: inout int,y: inout int) { x=y }
fn inner(x: inout int) { x=3 }
fn outer(x: inout int) { inner(&x); inner(&x) }
fn main() { var x=1; var y=x; pair(&x,&y); outer(&x) }
'''), [])
