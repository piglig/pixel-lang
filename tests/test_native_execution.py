"""Real subprocess acceptance for the native compiler execution lane."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from pixellang.compiler_service import CompilerService, Limits
from pixellang.model import PixelError
from pixellang.native_codegen import build_native
from pixellang.native_execution import NativeExecution
from tests.bytecode_helpers import op, program


@unittest.skipUnless(shutil.which('clang'), 'C compiler required')
class NativeExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory=tempfile.TemporaryDirectory()
        cls.root=Path(cls.directory.name)
        cls.code=program([])
        cls.code['constants']=[{'type':'int','value':42}]
        cls.code['functions']={'main':dict(params=[],locals=0,result='unit',instructions=[
            op('PUSH',0),op('PRINT'),op('PUSH',0),op('RETURN')])}
        cls.executable=build_native(cls.code,cls.root/'success',optimization=0)
        loop=program([])
        loop['functions']={'main':dict(params=[],locals=0,result='unit',instructions=[op('JUMP',0)])}
        cls.loop=build_native(loop,cls.root/'loop',optimization=0)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def execute(self, executable=None, **overrides):
        runner=NativeExecution(executable or self.executable)
        options=dict(root=self.root,steps=1000000000,output_chars=1000,file_bytes=1,
                     cancelled=lambda:False,deadline=time.monotonic()+5)
        options.update(overrides)
        events=runner.execute('{}',**options)
        return events,runner.steps

    def test_native_events_and_exact_steps(self):
        self.assertEqual(self.execute(),([42],4))

    def test_instruction_and_output_budgets(self):
        for options,code in [(dict(steps=2),'service.steps'),
                             (dict(output_chars=1),'service.native-execution')]:
            with self.subTest(options=options),self.assertRaises(PixelError) as caught:
                self.execute(**options)
            self.assertEqual(caught.exception.code,code)

    def test_running_cancellation_deadline_and_process_reaping(self):
        import subprocess
        original=subprocess.Popen
        children=[]
        def spawn(*args,**kwargs):
            child=original(*args,**kwargs)
            children.append(child)
            return child
        for expected in ['service.cancelled','service.timeout']:
            start=time.monotonic()
            options=(dict(cancelled=lambda:time.monotonic()-start>.04) if expected=='service.cancelled'
                     else dict(deadline=start+.04))
            with patch('pixellang.native_execution.subprocess.Popen',side_effect=spawn):
                with self.assertRaises(PixelError) as caught:
                    self.execute(self.loop,**options)
            self.assertEqual(caught.exception.code,expected)
            self.assertIsNotNone(children[-1].poll())
            self.assertLess(time.monotonic()-start,2)
        self.assertEqual(self.execute(),([42],4))

    def test_service_never_constructs_vm_and_accounts_cumulative_steps(self):
        with patch('pixellang.compiler_service.VM',side_effect=AssertionError('Interpreter fallback')):
            service=CompilerService(self.code,native_executable=self.executable,limits=Limits(steps=6))
        self.assertIsNone(service.vm)
        self.assertEqual(service._execute({},self.root,lambda:False,time.monotonic()+5),[42])
        self.assertEqual(service.work_steps,4)
        with self.assertRaises(PixelError) as caught:
            service._execute({},self.root,lambda:False,time.monotonic()+5)
        self.assertEqual(caught.exception.code,'service.steps')
        self.assertEqual(service.work_steps,6)

    def test_service_rejects_mismatched_artifact(self):
        changed=json.loads(json.dumps(self.code))
        changed['constants'][0]['value']=43
        with self.assertRaisesRegex(ValueError,'does not match'):
            CompilerService(changed,native_executable=self.executable)


@unittest.skipUnless(shutil.which('clang'), 'C compiler required')
class NativeCompilerServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.native_compiler import load_native_compiler
        cls.code=load_native_compiler(driver=None)
        cls.directory=tempfile.TemporaryDirectory()
        cls.executable=build_native(cls.code,Path(cls.directory.name)/'compiler',optimization=1,max_depth=1024)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_deep_statement_nesting_reports_diagnostic_without_native_crash(self):
        source='fn main() {'+'if true {'*140+'}'*141
        runner=NativeExecution(self.executable)
        events=runner.execute(json.dumps(dict(source='test.pxl',text=source,operation='parse')),
            root=Path(self.directory.name),steps=40_000_000,output_chars=64_000_000,
            file_bytes=64_000_000,cancelled=lambda:False,deadline=time.monotonic()+30)
        self.assertEqual(len(events[0]['diagnostics']),1)
        self.assertIn('nesting exceeds',events[0]['diagnostics'][0]['message'])
        self.assertGreater(runner.steps,0)

    def test_type_nesting_reports_source_diagnostic_before_runtime_depth_limit(self):
        service=CompilerService(self.code,native_executable=self.executable)
        source='fn accept(value:'+'fn('*129+'int'+')'*129+'){}\nfn main(){}'
        result=service.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(result['status'],'diagnostics',result)
        self.assertEqual(result['diagnostics'][0]['message'],'Type nesting exceeds 128')
        self.assertEqual(result['diagnostics'][0]['source'],'main.pxl')

    def test_multifile_debug_png_recovery_and_diagnostics_without_vm(self):
        from pixellang.vm import VM
        files={'main.pxl':'import "lib.pxl" as lib\neffect fn main(){print(lib.twice(21))}',
               'lib.pxl':'export fn twice(n:int)->int=n*2'}
        with patch('pixellang.compiler_service.VM',side_effect=AssertionError('Interpreter fallback')):
            service=CompilerService(self.code,native_executable=self.executable)
            for operation in ['check','compile','debug-compile','export-png']:
                response=service.request(dict(version=1,operation=operation,files=files))
                self.assertEqual(response['status'],'ok',response)
                self.assertGreater(response['metrics']['steps'],0)
                if operation=='compile':
                    self.assertEqual(VM(response['result']['bytecode']).run(),[42])
            recovered=service.request(dict(version=1,operation='compile-png',image=response['result']['image']))
            self.assertEqual(recovered['status'],'ok',recovered)
            self.assertEqual(VM(recovered['result']['bytecode']).run(),[42])
            invalid=service.request(dict(version=1,operation='check',files={'main.pxl':'effect fn main(){print(missing)}'}))
            self.assertEqual(invalid['status'],'diagnostics',invalid)
            self.assertTrue(invalid['diagnostics'])

    def test_cancel_then_reuse_service(self):
        service=CompilerService(self.code,native_executable=self.executable)
        polls=0
        def cancelled():
            nonlocal polls
            polls+=1
            return polls>8
        files={'main.pxl':'\n'.join(f'fn f{i}()->int={i}' for i in range(500))+'\neffect fn main(){print(42)}'}
        response=service.request(dict(version=1,operation='compile',files=files),cancelled=cancelled)
        self.assertEqual(response['status'],'cancelled',response)
        result=service.request(dict(version=1,operation='compile',files={'main.pxl':'effect fn main(){print(42)}'}))
        self.assertEqual(result['status'],'ok',result)

    def test_supervised_native_worker_debug_cache_and_restart(self):
        from pixellang.bootstrap import normalized
        from pixellang.compiler_worker import CompilerWorker
        from pixellang.vm import VM
        artifact=Path(self.directory.name)/'compiler.json'
        artifact.write_text(normalized(self.code))
        request=dict(version=1,operation='debug-compile',files={'main.pxl':'effect fn main(){print(42)}'})
        with CompilerWorker(artifact,native_executable=self.executable) as worker:
            first=worker.request(request)
            self.assertEqual(first['status'],'ok',first)
            self.assertEqual(VM(first['result']['bytecode']).run(),[42])
            pid=worker.process.pid
            second=worker.request(request)
            self.assertEqual(second['status'],'ok',second)
            self.assertTrue(second['metrics']['artifactCacheHit'])
            self.assertEqual(worker.process.pid,pid)
            worker._stop()
            third=worker.request(request)
            self.assertEqual(third['status'],'ok',third)
            self.assertFalse(third['metrics']['artifactCacheHit'])

    def test_workstation_json_build_uses_selected_native_compiler(self):
        from pixellang.bootstrap import normalized
        from pixellang.vm import VM
        artifact=Path(self.directory.name)/'compiler.json'
        artifact.write_text(normalized(self.code))
        request=dict(version=1,id='native-build',method='build',args={
            'files':{'main.pxl':'effect fn main(){print(42)}'}})
        process=subprocess.Popen([sys.executable,'-m','pixellang.workstation',
            '--compiler-artifact',str(artifact),'--native-compiler',str(self.executable)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            process.stdin.write(json.dumps(request)+'\n');process.stdin.flush()
            response=json.loads(process.stdout.readline())
            self.assertEqual(response['status'],'ok',response)
            self.assertEqual(response['id'],'native-build')
            self.assertNotIn('bytecode',response['result']['inspection'])
            process.stdin.write(json.dumps(dict(version=1,id='inspect',method='inspect-artifact',
                args={'session':response['result']['session']}))+'\n');process.stdin.flush()
            inspected=json.loads(process.stdout.readline())
            self.assertEqual(inspected['status'],'ok',inspected)
            self.assertEqual(VM(inspected['result']['bytecode']).run(),[42])
            _,stderr=process.communicate(timeout=30)
            self.assertEqual(process.returncode,0,stderr)
        finally:
            if process.poll() is None:
                process.kill();process.communicate()
