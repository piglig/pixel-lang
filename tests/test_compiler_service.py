from contextlib import ExitStack
from pathlib import Path
import unittest
from unittest.mock import patch

from pixellang.bootstrap_image import HOST_STAGES
from pixellang.compiler_service import CompilerService, Limits
from pixellang.project import compile_project
from pixellang.vm import VM


class CompilerServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).resolve().parents[1]
        cls.code=compile_project({p.name:p.read_text() for p in (root/'selfhost').glob('*.pxl')})[1].bytecode

    def setUp(self):
        self.service=CompilerService(self.code)

    def call(self, operation, **args):
        with ExitStack() as stack:
            for target in (*HOST_STAGES,'pixellang.project.compile_project'):
                stack.enter_context(patch(target,side_effect=AssertionError('Host frontend used')))
            return self.service.request(dict(version=1,id='test',revision=7,operation=operation,**args))

    def test_text_image_and_pixel_operations_execute_only_pixellang(self):
        files={'main.pxl':'import "lib.pxl" as lib\nfn main(){print(lib.twice(21))}',
               'lib.pxl':'export fn twice(n:int)->int=n*2'}
        checked=self.call('check',files=files)
        self.assertEqual((checked['status'],checked['revision']),('ok',7))
        compiled=self.call('compile',files=files)
        self.assertEqual(compiled['status'],'ok',compiled)
        self.assertEqual(VM(compiled['result']['bytecode']).run(),[42])
        pixels=self.call('pixels',files=files)
        self.assertEqual(pixels['status'],'ok',pixels)
        self.assertTrue(pixels['result']['document']['pixels'])
        png=self.call('export-png',files=files)
        self.assertEqual(png['status'],'ok',png)
        self.assertTrue(png['result']['image'].startswith(b'\x89PNG'))
        recovered=self.call('compile-png',image=png['result']['image'])
        self.assertEqual(recovered['status'],'ok',recovered)
        self.assertEqual(VM(recovered['result']['bytecode']).run(),[42])

    def test_diagnostics_retain_source_and_unicode_positions(self):
        result=self.call('check',files={'main.pxl':'fn main(){\n let x: int = "雪"\n}'})
        self.assertEqual(result['status'],'diagnostics',result)
        diagnostic=result['diagnostics'][0]
        self.assertEqual(diagnostic['source'],'main.pxl')
        self.assertEqual(diagnostic['line'],2)
        self.assertGreater(diagnostic['end'],diagnostic['start'])
        self.assertNotIn('bytecode',result.get('result') or {})

    def test_running_cancellation_and_reset(self):
        polls=[]
        def cancelled():
            polls.append(1)
            return len(polls)>8
        result=self.service.request(dict(version=1,operation='compile',files={
            'main.pxl':'\n'.join(f'fn f{i}()->int={i}' for i in range(100))+'\nfn main(){print(42)}'}),cancelled=cancelled)
        self.assertEqual(result['status'],'cancelled',result)
        self.assertGreater(result['metrics']['steps'],0)
        again=self.call('compile',files={'main.pxl':'fn main(){print(63)}'})
        self.assertEqual(again['status'],'ok',again)
        self.assertEqual(VM(again['result']['bytecode']).run(),[63])

    def test_limits_and_protocol_fail_without_success(self):
        for limits in [dict(seconds=float('nan')),dict(seconds=True),dict(steps=0),dict(file_bytes=64_000_001)]:
            with self.assertRaises(ValueError): Limits(**limits)
        for request in [None,dict(version=2),dict(version=True),dict(version=1,operation='unknown')]:
            result=self.service.request(request)
            self.assertEqual(result['status'],'error')
            self.assertEqual(result['metrics']['steps'],0)
        self.service=CompilerService(self.code,limits=Limits(seconds=0.000001))
        result=self.call('check',files={'main.pxl':'fn main(){}'})
        self.assertEqual(result['status'],'timeout',result)

    def test_unchanged_parses_reused_and_changed_module_rebuilt(self):
        files={'main.pxl':'import "lib.pxl" as lib\nfn main(){print(lib.twice(21))}',
               'lib.pxl':'export fn twice(n:int)->int=n*2'}
        cold=self.call('compile',files=files)
        self.assertEqual(cold['status'],'ok',cold)
        self.assertEqual(cold['metrics']['parsedModules'],2)
        warm=self.call('compile',files=files)
        self.assertEqual(warm['metrics']['parsedModules'],0)
        self.assertEqual(warm['metrics']['parseHits'],2)
        self.assertEqual(cold['result'],warm['result'])
        files['lib.pxl']='export fn twice(n:int)->int=n*3'
        changed=self.call('compile',files=files)
        self.assertEqual(changed['metrics']['parsedModules'],1)
        self.assertEqual(changed['metrics']['parseHits'],1)
        uncached=self.call('compile',files=files,incremental=False)
        self.assertEqual(uncached['result'],changed['result'])
        self.assertEqual(VM(changed['result']['bytecode']).run(),[63])
        files['lib.pxl']='export fn twice(n:int)->int="wrong"'
        invalid=self.call('check',files=files)
        self.assertEqual(invalid['status'],'diagnostics',invalid)
        self.assertEqual(invalid['diagnostics'][0]['source'],'lib.pxl')
        self.assertEqual(invalid['diagnostics'],self.call('check',files=files,incremental=False)['diagnostics'])

    def test_interface_changes_recheck_transitive_dependents_and_preserve_errors(self):
        files={
            'main.pxl':'import "middle.pxl" as middle\nfn main(){print(middle.value())}',
            'middle.pxl':'import "lib.pxl" as lib\nexport fn value()->int=lib.value()',
            'lib.pxl':'export fn value()->int=42'}
        cold=self.call('check',files=files)
        self.assertEqual(set(cold['result']['checkedModules']),set(files))
        warm=self.call('check',files=files)
        self.assertEqual(warm['result']['checkedModules'],[])
        files['lib.pxl']='export fn value()->int=63'
        body=self.call('check',files=files)
        self.assertEqual(body['result']['checkedModules'],['lib.pxl'])
        files['lib.pxl']='export fn value()->string="changed"'
        interface=self.call('check',files=files)
        self.assertEqual(set(interface['result']['checkedModules']),set(files))
        self.assertEqual(interface['status'],'diagnostics',interface)
        self.assertEqual(interface['diagnostics'][0]['source'],'middle.pxl')
        warm_error=self.call('check',files=files)
        self.assertEqual(warm_error['result']['checkedModules'],[])
        self.assertEqual(warm_error['diagnostics'],interface['diagnostics'])
        uncached=self.call('check',files=files,incremental=False)
        self.assertEqual(uncached['diagnostics'],interface['diagnostics'])
        del files['lib.pxl']
        missing=self.call('check',files=files)
        self.assertTrue(missing['result']['globalFailure'])
        self.assertEqual(missing['diagnostics'],self.call('check',files=files,incremental=False)['diagnostics'])
        files['lib.pxl']='export fn value()->int=42'
        fixed=self.call('check',files=files)
        self.assertEqual(fixed['status'],'ok',fixed)
        self.assertEqual(set(fixed['result']['checkedModules']),set(files))

    def test_normalized_paths_do_not_skip_body_checks(self):
        files={'./main.pxl':'fn main(){ let x:int="bad" }'}
        cached=self.call('check',files=files)
        direct=self.call('check',files=files,incremental=False)
        self.assertEqual(cached['status'],'diagnostics',cached)
        self.assertEqual(cached['diagnostics'],direct['diagnostics'])

    def test_debug_bundle_matches_separate_artifacts_and_uses_less_work(self):
        files={'main.pxl':'import "lib.pxl" as lib\nfn main(){print(lib.twice(21))}',
               'lib.pxl':'export fn twice(n:int)->int=n*2'}
        separate={op:self.call(op,files=files,scale=4) for op in
                  ('debug-compile','debug-pixels','inspect-ir','export-debug-png')}
        for response in separate.values():self.assertEqual(response['status'],'ok',response)
        bundle=self.call('debug-bundle',files=files,includeIR=True,includePNG=True,scale=4)
        self.assertEqual(bundle['status'],'ok',bundle)
        result=bundle['result']
        for name,op in [('bytecode','debug-compile'),('debug','debug-compile'),
                        ('document','debug-pixels'),('ir','inspect-ir'),('image','export-debug-png')]:
            self.assertEqual(result[name],separate[op]['result'][name],name)
        self.assertLess(bundle['metrics']['steps'],sum(r['metrics']['steps'] for r in separate.values()))
        from pixellang.debug_artifact import prepare_debug
        artifact=prepare_debug(result['bytecode'],result['debug'],result['document'])
        self.assertEqual(VM(artifact.bytecode).run(),[42])
        lazy=self.call('debug-bundle',files=files)
        self.assertEqual(lazy['status'],'ok',lazy)
        self.assertNotIn('ir',lazy['result']);self.assertNotIn('image',lazy['result'])
        self.assertEqual(lazy['result']['document'],result['document'])
        self.assertLess(lazy['metrics']['steps'],bundle['metrics']['steps'])

    def test_debug_bundle_diagnostics_and_options(self):
        failed=self.call('debug-bundle',files={'main.pxl':'fn main(){let x:int="bad"}'},includePNG=True)
        self.assertEqual(failed['status'],'diagnostics',failed)
        self.assertIsNone(failed['result'])
        invalid=self.call('debug-bundle',files={'main.pxl':'fn main(){}'},includeIR='yes')
        self.assertEqual(invalid['status'],'error',invalid)

    def test_bundle_cache_is_exact_bounded_and_returns_isolated_values(self):
        files={'main.pxl':'fn main(){print(42)}'}
        cold=self.call('debug-bundle',files=files)
        warm=self.call('debug-bundle',files=files)
        self.assertTrue(warm['metrics']['artifactCacheHit'])
        self.assertEqual(warm['metrics']['steps'],0)
        warm['result']['bytecode']['functions'].clear()
        self.assertEqual(self.call('debug-bundle',files=files)['result'],cold['result'])
        changed=self.call('debug-bundle',files={'main.pxl':'fn main(){print(63)}'})
        self.assertFalse(changed['metrics']['artifactCacheHit'])
        self.assertEqual(VM(changed['result']['bytecode']).run(),[63])
        optional=self.call('debug-bundle',files=files,includeIR=True)
        self.assertFalse(optional['metrics']['artifactCacheHit'])
        uncached=self.call('debug-bundle',files=files,incremental=False)
        self.assertFalse(uncached['metrics']['artifactCacheHit'])
        self.assertEqual(uncached['result'],cold['result'])
        cancelled=self.service.request(dict(version=1,operation='debug-bundle',files=files),cancelled=lambda:True)
        self.assertEqual(cancelled['status'],'cancelled')
        self.assertLessEqual(self.service.artifact_bytes,32_000_000)

    def test_artifact_cache_rejects_malformed_requests(self):
        valid=self.call('debug-bundle',files={'main.pxl':'fn main(){print(42)}'})
        self.assertEqual(valid['status'],'ok',valid)
        invalid=self.call('debug-bundle',files={1:'fn main(){print(42)}'})
        self.assertEqual(invalid['status'],'error',invalid)
        self.assertFalse(invalid['metrics']['artifactCacheHit'])
