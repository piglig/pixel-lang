from contextlib import ExitStack
from pathlib import Path
import unittest
from unittest.mock import patch

from pixellang.bootstrap_image import HOST_STAGES
from pixellang.compiler_service import CompilerService, Limits
from pixellang.vm import VM


class CompilerServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.native_compiler import load_native_compiler
        cls.code=load_native_compiler(driver=None)

    def setUp(self):
        self.service=self.make_service()

    def make_service(self, *, limits=None):
        service=CompilerService.installed(limits=limits)
        self.assertIsNone(service.vm)
        return service

    def call(self, operation, **args):
        with ExitStack() as stack:
            for target in HOST_STAGES:
                stack.enter_context(patch(target,side_effect=AssertionError('Host frontend used')))
            return self.service.request(dict(version=1,id='test',revision=7,operation=operation,**args))

    def test_text_image_and_pixel_operations_execute_only_pixellang(self):
        files={'main.pxl':'import "lib.pxl" as lib\neffect fn main(){print(lib.twice(21))}',
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

    def test_reference_vm_checks_complete_inputs_without_host_incremental_state(self):
        service=CompilerService(self.code)
        self.addCleanup(service.close)
        files={'main.pxl':'import "lib.pxl" as lib\neffect fn main(){print(lib.value())}',
               'lib.pxl':'export fn value()->int=1'}
        with patch.object(service,'_execute',wraps=service._execute) as execute:
            cold=service.request(dict(version=1,operation='check',files=files))
            warm=service.request(dict(version=1,operation='check',files=files))
            self.assertEqual(cold['status'],'ok',cold)
            self.assertEqual(warm['result'],cold['result'])
            self.assertEqual(set(warm['result']['checkedModules']),set(files))
            self.assertEqual(warm['metrics']['parseHits'],0)
            files['lib.pxl']='export fn value()->int="bad"'
            invalid=service.request(dict(version=1,operation='check',files=files))
            self.assertEqual(invalid['status'],'diagnostics',invalid)
            fresh=service.request(dict(version=1,operation='check',files=files,incremental=False))
            self.assertEqual(invalid['diagnostics'],fresh['diagnostics'])
            self.assertEqual([call.args[0]['operation'] for call in execute.call_args_list],['check-summary']*4)
            self.assertTrue(all('parsedFiles' not in call.args[0] and 'checkModules' not in call.args[0]
                                for call in execute.call_args_list))

    def test_diagnostics_retain_source_and_unicode_positions(self):
        result=self.call('check',files={'main.pxl':'fn main(){\n let x: int = "雪"\n}'})
        self.assertEqual(result['status'],'diagnostics',result)
        diagnostic=result['diagnostics'][0]
        self.assertEqual(diagnostic['source'],'main.pxl')
        self.assertEqual(diagnostic['line'],2)
        self.assertGreater(diagnostic['end'],diagnostic['start'])
        self.assertNotIn('bytecode',result.get('result') or {})

    def test_native_ir_reuse_is_default_and_matches_fresh_compilation(self):
        files={'main.pxl':'effect fn main(){print(42)}'}
        plain=self.call('compile',files=files,incremental=False)
        self.assertEqual(plain['status'],'ok',plain)
        self.assertEqual(plain['metrics']['residentIRNodes'],0)
        cached=self.call('compile',files=files,incremental=True)
        self.assertEqual(cached['result'],plain['result'])
        self.assertGreater(cached['metrics']['residentIRNodes'],0)
        warm=self.call('compile',files=files,incremental=True)
        self.assertGreater(warm['metrics']['irCacheHits'],0)
        default=self.call('compile',files=files)
        self.assertEqual(default['result'],warm['result'])
        self.assertGreater(default['metrics']['irCacheHits'],0)
        self.assertGreater(default['metrics']['parseHits'],0)

    def test_native_test_metadata_and_selected_entry(self):
        files={'main.pxl': 'export effect fn test_first(){print(1)}\neffect fn test_second(){print(2)}\neffect fn main(){print(3)}'}
        info=self.call('module-info',files=files)
        self.assertEqual(info['status'],'ok',info)
        self.assertEqual(info['result'],{'imports':[], 'hasMain':True,
                         'tests':[{'name':'test_first','valid':True},{'name':'test_second','valid':True}]})
        # Alternate entries in one service to catch reuse of the previous entry.
        for name, expected in [('test_first',1),('test_second',2),('main',3),('test_first',1)]:
            compiled=self.call('compile',files=files,entryFunction=name)
            self.assertEqual(compiled['status'],'ok',compiled)
            self.assertEqual(VM(compiled['result']['bytecode']).run(),[expected])

    def test_pixel_document_compilation_uses_native_decoder_and_budget(self):
        pixels=self.call('pixels',files={'main.pxl':'effect fn main(){print(42)}'})['result']['document']
        result=self.call('compile-pixels',document=pixels)
        self.assertEqual(result['status'],'ok',result)
        self.assertEqual(VM(result['result']['bytecode']).run(),[42])
        code=result['result']['bytecode']
        self.assertIn('PRINT',[instruction['op'] for instruction in code['functions'][code['entry'][0]]['instructions']])
        for document in ([],None,{'invalid':float('nan')}):
            self.assertEqual(self.call('compile-pixels',document=document)['status'],'error')
        self.service=self.make_service(limits=Limits(file_bytes=10))
        result=self.call('compile-pixels',document=pixels)
        self.assertEqual(result['status'],'error',result)
        self.assertIn('file budget',result['error']['message'])

    def test_selected_entry_validation(self):
        files={'main.pxl': 'fn test_generic[T]() {}\nfn test_args(x:int) {}\nfn test_result()->int=1\nfn helper(){fn nested(){}}'}
        for name in ['missing','nested','test_generic','test_args','test_result']:
            with self.subTest(name=name):
                result=self.call('compile',files=files,entryFunction=name)
                self.assertEqual(result['status'],'diagnostics',result)
                self.assertNotIn('bytecode',result.get('result') or {})
        for operation, name in [('compile',''),('compile',3),('module-info','test_args')]:
            result=self.call(operation,files=files,entryFunction=name)
            self.assertEqual(result['status'],'error',result)

    def test_running_cancellation_and_reset(self):
        # Reference interpreter internals; native child reaping is covered by
        # test_native_execution. Other service tests use the installed backend.
        self.service=CompilerService(self.code)
        polls=[]
        def cancelled():
            polls.append(1)
            return len(polls)>8
        result=self.service.request(dict(version=1,operation='compile',files={
            'main.pxl':'\n'.join(f'fn f{i}()->int={i}' for i in range(100))+'\neffect fn main(){print(42)}'}),cancelled=cancelled)
        self.assertEqual(result['status'],'cancelled',result)
        self.assertGreater(result['metrics']['steps'],0)
        self.assertEqual(self.service.vm.heap, {})
        self.assertEqual(self.service.vm.input_text, '')
        self.assertIsNone(self.service.vm.file_access)
        again=self.call('compile',files={'main.pxl':'effect fn main(){print(63)}'})
        self.assertEqual(again['status'],'ok',again)
        self.assertEqual(VM(again['result']['bytecode']).run(),[63])

    def test_completed_requests_release_execution_state_without_invalidating_results(self):
        self.service=CompilerService(self.code)
        first=self.call('compile',files={'main.pxl':'effect fn main(){print([1,2,3])}'})
        self.assertEqual(first['status'],'ok',first)
        self.assertGreater(first['metrics']['steps'],0)
        self.assertEqual(self.service.vm.heap,{})
        self.assertEqual(self.service.vm.output,[])
        second=self.call('compile',files={'main.pxl':'effect fn main(){print(42)}'})
        self.assertEqual(second['status'],'ok',second)
        self.assertEqual(VM(first['result']['bytecode']).run(),[[1,2,3]])
        self.assertEqual(VM(second['result']['bytecode']).run(),[42])

    def test_limits_and_protocol_fail_without_success(self):
        for limits in [dict(seconds=float('nan')),dict(seconds=True),dict(steps=0),dict(file_bytes=64_000_001)]:
            with self.assertRaises(ValueError): Limits(**limits)
        for request in [None,dict(version=2),dict(version=True),dict(version=1,operation='unknown')]:
            result=self.service.request(request)
            self.assertEqual(result['status'],'error')
            self.assertEqual(result['metrics']['steps'],0)
        self.service=self.make_service(limits=Limits(seconds=0.000001))
        result=self.call('check',files={'main.pxl':'fn main(){}'})
        self.assertEqual(result['status'],'timeout',result)

    def test_unchanged_parses_reused_and_changed_module_rebuilt(self):
        files={'main.pxl':'import "lib.pxl" as lib\neffect fn main(){print(lib.twice(21))}',
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

    def test_resident_parse_reuse_does_not_encode_host_transport(self):
        files={'main.pxl':'import "lib.pxl" as lib\neffect fn main(){print(lib.value())}',
               'lib.pxl':'export fn value()->string="猫🌍"'}
        cold=self.call('debug-compile',files=files,reuseArtifacts=False)
        self.assertEqual(cold['status'],'ok',cold)
        with patch('pixellang.image_transport.encode_parsed',side_effect=AssertionError('Cached AST reencoded')):
            warm=self.call('debug-compile',files=files,reuseArtifacts=False)
        self.assertEqual(warm['status'],'ok',warm)
        self.assertEqual(warm['result'],cold['result'])
        self.assertEqual(warm['metrics']['parseHits'],2)
        self.assertEqual(VM(warm['result']['bytecode']).run(),['猫🌍'])
        self.assertGreater(warm['metrics']['residentCacheNodes'],0)

    def test_interface_changes_recheck_transitive_dependents_and_preserve_errors(self):
        files={
            'main.pxl':'import "middle.pxl" as middle\neffect fn main(){print(middle.value())}',
            'middle.pxl':'import "lib.pxl" as lib\nexport fn value()->int=lib.value()',
            'lib.pxl':'export fn value()->int=42'}
        cold=self.call('check',files=files)
        self.assertEqual(set(cold['result']['checkedModules']),set(files))
        warm=self.call('check',files=files)
        self.assertEqual(warm['metrics']['semanticCheckedFunctions'],0)
        self.assertEqual(warm['metrics']['semanticCacheHits'],3)
        files['lib.pxl']='export fn value()->int=63'
        body=self.call('check',files=files)
        self.assertEqual(body['metrics']['semanticCheckedFunctions'],1)
        self.assertEqual(body['metrics']['semanticCacheHits'],2)
        files['lib.pxl']='export fn value()->string="changed"'
        interface=self.call('check',files=files)
        self.assertEqual(set(interface['result']['checkedModules']),set(files))
        self.assertEqual(interface['status'],'diagnostics',interface)
        self.assertEqual(interface['diagnostics'][0]['source'],'middle.pxl')
        warm_error=self.call('check',files=files)
        self.assertGreater(warm_error['metrics']['semanticCheckedFunctions'],0)
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
        files={'main.pxl':'import "lib.pxl" as lib\neffect fn main(){print(lib.twice(21))}',
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
        files={'main.pxl':'effect fn main(){print(42)}'}
        cold=self.call('debug-bundle',files=files)
        warm=self.call('debug-bundle',files=files)
        self.assertTrue(warm['metrics']['artifactCacheHit'])
        self.assertEqual(warm['metrics']['steps'],0)
        warm['result']['bytecode']['functions'].clear()
        self.assertEqual(self.call('debug-bundle',files=files)['result'],cold['result'])
        changed=self.call('debug-bundle',files={'main.pxl':'effect fn main(){print(63)}'})
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

    def test_text_debug_cache_is_exact_isolated_and_cancellable(self):
        files={'main.pxl':'effect fn main(){print(42)}'}
        cold=self.call('debug-compile',files=files)
        self.assertEqual(cold['status'],'ok',cold)
        self.assertFalse(cold['metrics']['artifactCacheHit'])
        warm=self.call('debug-compile',files=files)
        self.assertTrue(warm['metrics']['artifactCacheHit'])
        self.assertEqual(warm['metrics']['steps'],0)
        warm['result']['bytecode']['functions'].clear()
        self.assertEqual(self.call('debug-compile',files=files)['result'],cold['result'])
        changed=self.call('debug-compile',files={'main.pxl':'effect fn main(){print(7)}'})
        self.assertFalse(changed['metrics']['artifactCacheHit'])
        self.assertEqual(VM(changed['result']['bytecode']).run(),[7])
        uncached=self.call('debug-compile',files=files,reuseArtifacts=False)
        self.assertFalse(uncached['metrics']['artifactCacheHit'])
        self.assertEqual(uncached['result'],cold['result'])
        cancelled=self.service.request(dict(version=1,operation='debug-compile',files=files),cancelled=lambda:True)
        self.assertEqual(cancelled['status'],'cancelled')
        invalid=self.call('debug-compile',files={1:'fn main(){}'})
        self.assertNotEqual(invalid['status'],'ok')
        self.assertFalse(invalid['metrics']['artifactCacheHit'])

    def test_large_project_debug_document_preserves_metadata(self):
        from pixellang.workspace import Workspace
        from pixellang.debug_artifact import prepare_debug
        from pixellang import jsonvalue
        from pixellang.model import PixelError
        root = Path(__file__).resolve().parents[1]
        workspace = Workspace(root / 'examples/ledger')
        files = {'std/' + path.name: path.read_text()
                 for path in (root / 'pixellang/stdlib').glob('*.pxl')}
        files.update(workspace.files)
        response = self.call('debug-bundle', files=files, entry=workspace.entry)
        self.assertEqual(response['status'], 'ok', response)
        result = response['result']
        document = result['document']
        # Real compiler documents can exceed a single language JSON value.
        # Preserve them without relaxing the user program's JSON limits.
        with self.assertRaises(PixelError):
            jsonvalue.from_host(document)
        for source in (document, *document['bundle']['modules'].values()):
            self.assertIsInstance(source['metadata']['source_map'], dict)
            self.assertIsInstance(source['metadata']['symbols'], dict)
            self.assertIsInstance(source['metadata']['filename'], str)
        artifact = prepare_debug(result['bytecode'], result['debug'], document)
        self.assertTrue(artifact.debug)

    def test_parse_cache_follows_reachable_imports_and_drops_removed_errors(self):
        files={'main.pxl':'import "bad.pxl" as bad\neffect fn main(){print(bad.Value())}',
               'bad.pxl':'export fn Value()->int="wrong"',
               'good.pxl':'export fn Value()->int=42',
               'unused.pxl':'this is deliberately not a program'}
        failed=self.call('check',files=files)
        self.assertEqual(failed['status'],'diagnostics',failed)
        self.assertEqual(failed['metrics']['parsedModules'],2)
        files['main.pxl']='import "good.pxl" as good\neffect fn main(){print(good.Value())}'
        fixed=self.call('check',files=files)
        self.assertEqual(fixed['status'],'ok',fixed)
        self.assertEqual(fixed['metrics']['parsedModules'],2)
        self.assertEqual(fixed['metrics']['parseHits'],0)
        built=self.call('compile',files=files)
        self.assertEqual(built['status'],'ok',built)
        self.assertEqual(built['metrics']['parseHits'],2)
        self.assertEqual(VM(built['result']['bytecode']).run(),[42])

    def test_artifact_cache_rejects_malformed_requests(self):
        valid=self.call('debug-bundle',files={'main.pxl':'effect fn main(){print(42)}'})
        self.assertEqual(valid['status'],'ok',valid)
        invalid=self.call('debug-bundle',files={1:'effect fn main(){print(42)}'})
        self.assertEqual(invalid['status'],'error',invalid)
        self.assertFalse(invalid['metrics']['artifactCacheHit'])
