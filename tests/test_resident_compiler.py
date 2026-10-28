"""Semantic queries survive independent requests in one native process."""
import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from pixellang.native_codegen import build_native
from pixellang.native_session import NativeSessionExecution
from tests.native_compiler import compile_native_sources

DRIVER = '''import "compiler_session.pxl" as compiler
fn Initialize() -> compiler.Session = compiler.Empty()
effect fn Advance(previous: compiler.Session) -> compiler.Session {
 var state=previous
 match jsonDecode::[map[string,string]](input()) {
  .Ok(files) => {
   let result=compiler.Check(files,"main.pxl",100000,&state)
   print([result.parsed,result.parseHits,result.checkedFunctions,result.semanticHits,state.revision,len(result.checked.diagnostics)])
  }, .Err(error) => { print(error); assert(false) }
 }
 return state
}
effect fn main() { let initial=Initialize(); let next=Advance(initial) }
'''

@unittest.skipUnless(shutil.which('clang'), 'C compiler required')
class ResidentCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files={p.name:p.read_text() for p in (Path(__file__).resolve().parents[1]/'selfhost').glob('*.pxl')}
        files['main.pxl']=DRIVER
        code=compile_native_sources(files)
        calls=[instruction['arg'] for instruction in code['functions'][code['entry'][0]]['instructions']
               if instruction['op']=='CALL']
        # The fixture entry contains exactly Initialize() and Advance(initial).
        if len(calls)!=2: raise AssertionError(calls)
        cls.directory=tempfile.TemporaryDirectory()
        cls.root=Path(cls.directory.name)
        cls.executable=build_native(code,cls.root/'compiler-session',optimization=1,max_depth=1024,
                                   session=dict(initialize=calls[0],advance=calls[1]))

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_queries_reuse_across_frames_and_failed_request_preserves_state(self):
        runner=NativeSessionExecution(self.executable)
        self.addCleanup(runner.close)
        def request(files):
            return runner.execute(json.dumps(files), root=self.root, steps=100_000_000,
                output_chars=1000000,file_bytes=1,cancelled=lambda:False,deadline=time.monotonic()+30)[0]
        files={'lib.pxl':'export fn value()->int=1',
               'main.pxl':'import "lib.pxl" as lib\nfn use()->int=lib.value()\nfn main(){}'}
        self.assertEqual(request(files),[2,0,3,0,1,0])
        process=runner.process
        self.assertEqual(request(files),[0,2,0,3,2,0])
        bad={**files,'lib.pxl':'export fn value()->string="bad"'}
        invalid=request(bad)
        self.assertGreater(invalid[-1],0)
        self.assertEqual(invalid[-2],2)
        self.assertEqual(request(files),[0,2,0,3,3,0])
        changed={**files,'lib.pxl':'export fn value()->int=2'}
        self.assertEqual(request(changed),[1,1,1,2,4,0])
        self.assertIs(runner.process,process)
