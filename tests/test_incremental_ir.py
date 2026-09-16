"""Cross-edit lowering reuse must equal fresh PixelLang compilation."""
from contextlib import ExitStack
from pathlib import Path
import unittest
from unittest.mock import patch

from pixellang.bootstrap_image import HOST_STAGES
from pixellang.compiler_service import CompilerService
from pixellang.project import compile_project
from pixellang.vm import VM


class IncrementalIRTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).resolve().parents[1]
        cls.code=compile_project({p.name:p.read_text() for p in (root/'selfhost').glob('*.pxl')})[1].bytecode

    def setUp(self):
        self.service=CompilerService(self.code)

    def call(self,files,**options):
        operation=options.pop('operation','compile')
        with ExitStack() as guards:
            for target in (*HOST_STAGES,'pixellang.project.compile_project'):
                guards.enter_context(patch(target,side_effect=AssertionError('Host frontend used')))
            return self.service.request(dict(version=1,operation=operation,files=files,
                                             reuseArtifacts=False,**options))

    def equal_fresh(self,files,expected,**options):
        cached=self.call(files,**options)
        fresh=self.call(files,reuseIR=False,**options)
        self.assertEqual(cached['status'],'ok',cached)
        self.assertEqual(fresh['status'],'ok',fresh)
        self.assertEqual(cached['result'],fresh['result'])
        self.assertEqual(cached['diagnostics'],fresh['diagnostics'])
        self.assertEqual(VM(cached['result']['bytecode']).run(),expected)
        return cached

    def warm(self,files):
        response=self.call(files)
        self.assertEqual(response['status'],'ok',response)
        self.assertGreater(response['metrics']['irCacheBytes'],0,response)

    def test_dependency_body_edit_reuses_caller(self):
        files={'main.pxl':'import "lib.pxl" as lib\nfn main(){print(lib.value())}',
               'lib.pxl':'export fn value()->int=42'}
        self.warm(files)
        files['lib.pxl']='export fn value()->int=63'
        result=self.equal_fresh(files,[63])
        self.assertGreater(result['metrics']['irCacheHits'],0)

    def test_declaration_position_changes_relocate_nominal_types(self):
        files={'main.pxl':'import "lib.pxl" as lib\nfn main(){let b=lib.Box{value:42};print(b.value)}',
               'lib.pxl':'fn before()->int=1\nexport record Box {value:int}'}
        self.warm(files)
        files['lib.pxl']='fn before()->int{let extra=[1,2,3];return len(extra)}\nexport record Box {value:int}'
        result=self.equal_fresh(files,[42])
        self.assertGreater(result['metrics']['irCacheRelocated'],0)

    def test_new_calls_relocate_unchanged_function_targets(self):
        files={'main.pxl':'import "lib.pxl" as lib\nfn main(){print(lib.last())}',
               'lib.pxl':'export fn first()->int=1\nfn leaf()->int=42\nexport fn last()->int=leaf()'}
        self.warm(files)
        files['main.pxl']='import "lib.pxl" as lib\nfn main(){print(lib.first());print(lib.last())}'
        result=self.equal_fresh(files,[1,42])
        self.assertGreater(result['metrics']['irCacheRelocated'],0)

    def test_module_insertion_and_removal_preserve_debug_bundle(self):
        files={'main.pxl':'import "lib.pxl" as lib\nfn main(){print(lib.value())}',
               'lib.pxl':'export fn value()->int=42'}
        self.warm(files)
        files['extra.pxl']='export fn value()->int=1'
        files['main.pxl']='import "extra.pxl" as extra\nimport "lib.pxl" as lib\nfn main(){print(extra.value());print(lib.value())}'
        result=self.equal_fresh(files,[1,42],operation='debug-bundle',includeIR=True,includePNG=True)
        self.assertGreater(result['metrics']['irCacheRelocated'],0)
        del files['extra.pxl']
        files['main.pxl']='import "lib.pxl" as lib\nfn main(){print(lib.value())}'
        self.equal_fresh(files,[42],operation='debug-bundle',includeIR=True,includePNG=True)

    def test_unrelated_interface_edit_keeps_other_modules_reusable(self):
        files={'main.pxl':'import "a.pxl" as a\nimport "b.pxl" as b\nfn main(){print(a.value());print(b.value())}',
               'a.pxl':'export fn value()->int=1','b.pxl':'export fn value()->int=42'}
        self.warm(files)
        files['a.pxl']+='\nexport fn unused()->string="new"'
        result=self.equal_fresh(files,[1,42])
        self.assertGreater(result['metrics']['irCacheHits'],0)

    def test_generic_instances_and_captured_closures_remap_independently(self):
        files={'main.pxl':'import "lib.pxl" as lib\nfn main(){let f=lib.make(42);print(f())}',
               'lib.pxl':'export fn make[T](value:T)->fn()->T{return fn()=value}'}
        self.warm(files)
        files['main.pxl']='import "lib.pxl" as lib\nfn main(){let g=lib.make("x");let f=lib.make(42);print(g());print(f())}'
        result=self.equal_fresh(files,['x',42])
        self.assertGreater(result['metrics']['irCacheRelocated'],0)
        files['main.pxl']='import "lib.pxl" as lib\nfn main(){let f=lib.make(42);print(f())}'
        self.equal_fresh(files,[42])
        files['lib.pxl']='export fn make[T](value:T)->fn()->T{let other=value;return fn()=other}'
        self.equal_fresh(files,[42])

    def test_relocation_does_not_rewrite_type_shaped_string_literals(self):
        literal='@0:1[] fn(@1:2)->(@3:4)'
        files={'main.pxl':'import "lib.pxl" as lib\nfn main(){print(lib.text())}',
               'lib.pxl':f'export fn text()->string="{literal}"'}
        self.warm(files)
        files['extra.pxl']='export fn extra()->int=1'
        files['main.pxl']='import "extra.pxl" as extra\nimport "lib.pxl" as lib\nfn main(){print(extra.extra());print(lib.text())}'
        result=self.equal_fresh(files,[1,literal])
        self.assertGreater(result['metrics']['irCacheRelocated'],0)

    def test_error_then_fix_does_not_publish_partial_cache(self):
        files={'main.pxl':'import "lib.pxl" as lib\nfn main(){print(lib.value()+1)}',
               'lib.pxl':'export fn value()->int=41'}
        self.warm(files)
        previous=self.service.ir_cache
        files['lib.pxl']='export fn value()->string="bad"'
        failure=self.call(files)
        self.assertEqual(failure['status'],'diagnostics',failure)
        self.assertEqual(failure['diagnostics'],self.call(files,reuseIR=False)['diagnostics'])
        self.assertIs(self.service.ir_cache,previous)
        files['lib.pxl']='export fn value()->int=42'
        self.equal_fresh(files,[43])

    def test_cancellation_during_publication_keeps_previous_snapshot(self):
        files={'main.pxl':'fn main(){print(42)}'}
        self.warm(files)
        previous=self.service.ir_cache
        cancelled=False
        retain=self.service._retain_ir_cache
        def intercept(root):
            nonlocal cancelled
            value=retain(root)
            cancelled=True
            return value
        with patch.object(self.service,'_retain_ir_cache',side_effect=intercept):
            result=self.service.request(dict(version=1,operation='compile',files=files),cancelled=lambda:cancelled)
        self.assertEqual(result['status'],'cancelled',result)
        self.assertIs(self.service.ir_cache,previous)

    def test_noncanonical_paths_use_full_lowering(self):
        files={'main.pxl':'import "lib.pxl" as lib\nfn main(){print(lib.value())}',
               './lib.pxl':'export fn value()->int=42'}
        first=self.equal_fresh(files,[42])
        self.assertEqual(first['metrics']['irCacheHits'],0)
        files['./lib.pxl']='export fn value()->string="changed"'
        result=self.equal_fresh(files,['changed'])
        self.assertEqual(result['metrics']['irCacheHits'],0)


if __name__=='__main__':unittest.main()
