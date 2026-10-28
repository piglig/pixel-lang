"""Value-based module construction and interface analysis through native code."""
import json
from pathlib import Path
import unittest
import tempfile
import time

from pixellang.code_image import CodeImage
from pixellang.vm import VM
from pixellang.native_codegen import build_native
from pixellang.native_execution import NativeExecution
from tests.selfhost_runtime import SELFHOST_MAX_STEPS, SELFHOST_MAX_OUTPUT_CHARS
from tests import test_selfhost_project as project_tests
from tests.native_compiler import compile_native_sources

ROOT = Path(__file__).resolve().parents[1]
DRIVER = '''import "project.pxl" as project
import "parser.pxl" as parser
import "model.pxl" as model
import "source_info.pxl" as info
record Request {operation:string, source:string, text:string, files:map[string,string]}
effect fn main() {
 match jsonDecode::[Request](input()) { .Ok(request)=>{Dispatch(request)}, .Err(error)=>{print(error.message)} }
}
effect fn Dispatch(request:Request) {
 if request.operation=="info" {print(info.Describe(parser.Parse(request.source,request.text),request.source));return}
 if request.operation=="editor" {
  print(project.BuildEditorParsed(request.files,request.source,map[string,model.Parsed]{},[request.text]));return
 }
 if request.operation=="cached" {
  var parsed=map[string,model.Parsed]{}
  for path,text in request.files {parsed[path]=parser.Parse(path,text)}
  print(project.BuildParsed(request.files,request.source,parsed));return
 }
 print(project.Build(request.files,request.source))
}'''


def driver():
    files={name:(ROOT/'selfhost'/name).read_text() for name in
           ('model.pxl','lexer.pxl','parser.pxl','project.pxl','source_info.pxl')}
    return compile_native_sources({**files,'main.pxl':DRIVER})


class NativeProjectValueTests(project_tests.SelfHostedProjectTests):
    @classmethod
    def setUpClass(cls):
        cls.files={p.name:p.read_text() for p in (ROOT/'selfhost').glob('*.pxl')}
        cls.bytecode=driver()
        cls.native_directory=tempfile.TemporaryDirectory(prefix='pixel-project-test-')
        cls.addClassCleanup(cls.native_directory.cleanup)
        cls.native=NativeExecution(build_native(cls.bytecode,Path(cls.native_directory.name)/'project',
                                               optimization=1,max_depth=1024))

    def request(self,operation,files,source='main.pxl',text=''):
        events=self.native.execute(json.dumps(dict(operation=operation,files=files,source=source,text=text)),
            root=Path(self.native_directory.name),steps=SELFHOST_MAX_STEPS,
            output_chars=SELFHOST_MAX_OUTPUT_CHARS,file_bytes=64_000_000,
            cancelled=lambda:False,deadline=time.monotonic()+120)
        return events[0]

    def build(self, files, entry='main.pxl', *, native=True):
        if not native:
            return super().build(files, entry, native=False)
        return self.request('project',files,source=entry)

    def test_native_project_matches_vm_for_valid_and_invalid_sources(self):
        for files in ({'main.pxl':'import "lib.pxl" as lib\nfn main(){}','lib.pxl':'export fn f()->int=1'},
                      {'main.pxl':'import "missing.pxl"\nfn main(){}'}):
            request=dict(operation='project',files=files,source='main.pxl',text='')
            expected=VM(self.bytecode,input_text=json.dumps(request),max_steps=20_000_000).run()[0]
            self.assertEqual(self.build(files),expected)

    def test_cached_syntax_preserves_order_and_diagnostics(self):
        for files in (
            {'main.pxl':'import "a.pxl"\nfn main(){}','a.pxl':'export fn work(){}'},
            {'main.pxl':'import "a.pxl"\nfn main(){}','a.pxl':'fn broken( {'},
        ):
            with self.subTest(files=files):
                self.assertEqual(self.request('cached',files),self.build(files))

    def test_editor_recovers_imports_and_includes_detached_roots(self):
        files={'main.pxl':'import "a.pxl"\nfn main(){}',
               'a.pxl':'fn broken(){let = 1}\nexport fn valid(){}',
               'detached.pxl':'export record Detached {value:int}'}
        result=self.request('editor',files,text='detached.pxl')
        self.assertTrue(result['diagnostics'])
        self.assertEqual([m['path'] for m in result['modules']],['a.pxl','main.pxl','detached.pxl'])
        self.assertIn('valid',[d['name'] for d in result['modules'][0]['declarations']])


class NativeSourceInfoValueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image=CodeImage(driver())

    def describe(self,source):
        return VM(self.image,input_text=json.dumps(dict(operation='info',source='app/main.pxl',
                  text=source,files={})),max_steps=20_000_000).run()[0]

    def test_imports_entry_and_test_signatures(self):
        info=self.describe('''import "../lib.pxl" as lib
effect fn main(){}
fn test_good(){}
export fn test_explicit()->unit{}
fn test_arg(value:int){}
fn test_generic[T](){}
fn test_result()->int=1''')
        self.assertEqual(info['imports'],['lib.pxl'])
        self.assertTrue(info['hasMain'])
        self.assertEqual(info['tests'],[dict(name=name,valid=valid) for name,valid in
                         [('test_good',True),('test_explicit',True),('test_arg',False),
                          ('test_generic',False),('test_result',False)]])

    def test_interface_tracks_contracts_but_not_scalar_implementation_literals(self):
        first=self.describe('export fn compute(value:int)->int=value+1')['interface']
        second=self.describe('export fn compute(value:int)->int=value+2')['interface']
        self.assertEqual(first,second)
        for source in ('export effect fn compute(value:int)->int=value+1',
                       'export fn compute(value:int)->string=string(value)',
                       'export fn compute(value:int)->int=other(value)'):
            with self.subTest(source=source):self.assertNotEqual(first,self.describe(source)['interface'])

    def test_nested_callable_and_container_surfaces_are_stable(self):
        first='export fn adapt[T](values:[T], callback:fn(T)->T)->[T] {let f=callback;return values}'
        info=self.describe(first)
        self.assertEqual(info,self.describe(first))
        changed=first.replace('let f=callback','let f=fn(value:T)->T=value')
        self.assertNotEqual(info['interface'],self.describe(changed)['interface'])
