"""Isolated parser integration, editor recovery and native/VM equivalence."""
import json
from pathlib import Path
import unittest
from pixellang.code_image import CodeImage
from pixellang.vm import VM
from tests.native_compiler import compile_native_sources, load_native_compiler, native_driver

ROOT = Path(__file__).resolve().parents[1]
DRIVER = '''import "parser.pxl" as parser
record Request { source: string, text: string, editor: bool }
effect fn main() {
 match jsonDecode::[Request](input()) {.Ok(request)=>{Parse(request)},.Err(error)=>{print(error)}}
}
effect fn Parse(request:Request) {
 if request.editor { print(parser.ParseEditor(request.source,request.text)) }
 else { print(parser.Parse(request.source,request.text)) }
}'''


class ParserBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files={name:(ROOT/'selfhost'/name).read_text() for name in ('model.pxl','lexer.pxl','parser.pxl','failures.pxl')}
        cls.image=CodeImage(compile_native_sources({**files,'main.pxl':DRIVER}))
        cls.reference=CodeImage(load_native_compiler(driver=None))
        cls.component_run=staticmethod(cls.enterClassContext(native_driver(cls.image)))
        cls.compiler_run=staticmethod(cls.enterClassContext(native_driver(cls.reference)))

    def parse(self, text, editor=False):
        return self.component_run(dict(source='main.pxl',text=text,editor=editor),
                                  steps=20_000_000,output_chars=16_000_000)[0]

    def reference_parse(self, text):
        return self.compiler_run(dict(operation='parse',source='main.pxl',text=text),
                                 steps=20_000_000,output_chars=16_000_000)[0]

    def vm_parse(self, text, editor=False):
        return VM(self.image,input_text=json.dumps(dict(source='main.pxl',text=text,editor=editor)),
                  max_steps=20_000_000,max_output_chars=16_000_000).run()[0]

    def test_nested_builders_preserve_ast_and_source_locations(self):
        examples=[
            'record Box[T] { value: T }\nfn copy[T](x: T)->Box[T] = Box::[T]{value:x}\neffect fn main(){var a=[copy(1)];a.append(copy(2));print(a)}',
            'fn edit(x: inout [int]) {x.append(1)}\neffect fn main(){var a=[1];edit(&a);let f=fn(x:int)=x+1;print(f(a[0]))}',
            'enum State { Ready, Done(value: int) }\nfn main(){let state=State.Done(1);match state {State.Ready=>{},State.Done(value)=>{let x=value}}}',
            'fn main(){for i in [1,2] {if i>0 {continue}else{break}}}',
        ]
        for source in examples:
            with self.subTest(source=source):
                actual=self.parse(source)
                expected=self.reference_parse(source)
                self.assertEqual({name:actual[name] for name in ('nodes','roots','diagnostics')},expected)
                self.assertEqual(actual['diagnostics'],[])


    def test_editor_recovery_retains_diagnostics_and_later_declarations(self):
        result=self.parse('fn broken() { let = 1 }\nfn valid() { let value=2 }',editor=True)
        self.assertTrue(result['diagnostics'])
        self.assertTrue(any(node['kind']=='fn' and node['text']=='valid' for node in result['nodes']))
        strict=self.parse('fn broken() { let = 1 }\nfn valid() {}')
        self.assertTrue(strict['diagnostics'])

    def test_expression_precedence_updates_and_declaration_shapes(self):
        examples = [
            'record R {items: [int], size: int}\nfn main(){let r=R{items:[1,2],size:2};let s=r with {items:[r.items[1]],size:1};let x=(1+2)*3-4/2;let ok=!(x<0)&&x!=1}',
            'fn main(){let m=map[string,[int]]{"a":[1,2],"b":[]};let row=m["a"];let f=effect fn(x: int)->int {return x};let y=f(row[0])}',
            'export enum Outcome[T] {Good(value:T),Bad(message:string)}\nexport fn id[T: Equatable](x:T)->T=x\nfn main(){}',
            'frame World = meters\nflow Delay() step(value:int)->int {state old:int=0;next old=value;output old}\nfn main(){}',
            'import "library.pxl" as library\nfn main(){var i=0;while i<3 {i+=1};for key,value in map[int,int]{1:2} {let copy=value}}',
        ]
        for source in examples:
            with self.subTest(source=source):
                actual=self.parse(source)
                expected=self.reference_parse(source)
                self.assertEqual({name:actual[name] for name in ('nodes','roots','diagnostics')},expected)
                self.assertEqual(actual['diagnostics'],[])

    def test_strict_error_boundaries_preserve_diagnostics(self):
        for source in ('fn main(){let x=}', 'fn main(){let x=[1,2}',
                       'record R {field: }', 'fn main(){match Option.Some(1) {Option.Some(x) {}}}',
                       'fn main(){let x=1 let y=2}', 'fn main(){let x="bad\\q"}'):
            with self.subTest(source=source):
                actual=self.parse(source)
                expected=self.reference_parse(source)
                self.assertEqual({name:actual[name] for name in ('nodes','roots','diagnostics')},expected)
                self.assertTrue(actual['diagnostics'])

    def test_compiler_sources_preserve_ast_and_locations(self):
        # Compare isolated and integrated parsing of the current source corpus.
        for path in sorted((ROOT/'selfhost').glob('*.pxl')):
            with self.subTest(source=path.name):
                source=path.read_text()
                actual=self.component_run(dict(source=path.name,text=source,editor=False),
                    steps=100_000_000,output_chars=32_000_000)[0]
                expected=self.compiler_run(dict(operation='parse',source=path.name,text=source),
                    steps=100_000_000,output_chars=32_000_000)[0]
                self.assertEqual({name:actual[name] for name in ('nodes','roots','diagnostics')},expected)
                self.assertEqual(actual['diagnostics'],[])

    def test_native_component_matches_reference_vm(self):
        for source in ('fn main(){let f=fn(x:int)=x+1;let y=f(2)}','fn main(){let = }'):
            for editor in (False, True):
                with self.subTest(source=source,editor=editor):
                    self.assertEqual(self.parse(source,editor),self.vm_parse(source,editor))
