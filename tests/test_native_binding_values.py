"""The native binder uses value collections with explicit scoped updates."""
import json
from pathlib import Path

from pixellang.code_image import CodeImage
from pixellang.vm import VM
from tests import test_selfhost_bindings as binding_tests
from tests.native_compiler import compile_native_sources

ROOT=Path(__file__).resolve().parents[1]
DRIVER='''import "project.pxl" as project
import "bindings.pxl" as bindings
record Request {files:map[string,string], source:string, text:string, operation:string}
effect fn RunTest()->Result[int,Error] {let request=(jsonDecode::[Request](input()))?;print(bindings.Bind(project.Build(request.files,request.source)))
 return .Ok(0)
}
effect fn main(){match RunTest(){.Ok(done)=>{},.Err(error)=>{print(error)}}}
'''


class NativeBindingValueTests(binding_tests.SelfHostedBindingTests):
    @classmethod
    def setUpClass(cls):
        cls.files={p.name:p.read_text() for p in (ROOT/'selfhost').glob('*.pxl')}
        files={name:cls.files[name] for name in
               ('model.pxl','lexer.pxl','parser.pxl','project.pxl','bindings.pxl')}
        cls.bytecode=compile_native_sources({**files,'main.pxl':DRIVER})
        cls.reference=CodeImage(json.loads((ROOT/'pixellang/artifacts/compiler.json').read_text()))

    def test_name_tables_captures_and_references_match_verified_binder(self):
        programs=[
            {'main.pxl':'''fn update(value:inout int){value+=1}
effect fn main(){var x=1;update(&x);let f=fn()->int=x;if true {let x=2;print(x)};print(f())}'''},
            {'main.pxl':'''import "lib.pxl" as lib
effect fn main(){let pair=lib.Pair{value:1};print(pair.value);lib.work()}''',
             'lib.pxl':'export record Pair {value:int}\nexport fn work(){}'},
            {'main.pxl':'''fn run(value:inout int){let f=fn()->int=value}
fn main(){let x=1;let x=2;missing()}'''},
        ]
        for files in programs:
            with self.subTest(files=files):
                expected=VM(self.reference,input_text=json.dumps(dict(operation='bind',source='main.pxl',
                            text='',files=files)),max_steps=20_000_000,max_output_chars=16_000_000).run()[0]
                actual=self.bind(files)
                summary={name:actual[name] for name in ('scopes','symbols','references','captures','diagnostics')}
                summary['modules']=[module['path'] for module in actual['project']['modules']]
                self.assertEqual(summary,expected)
