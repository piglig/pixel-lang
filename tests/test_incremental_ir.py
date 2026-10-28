"""Cross-edit lowering reuse must equal fresh PixelLang compilation."""
from contextlib import ExitStack
from pathlib import Path
import unittest
from unittest.mock import patch

from pixellang.bootstrap_image import HOST_STAGES
from pixellang.compiler_service import CompilerService
from pixellang.compiler_client import CompilerClient
from pixellang.compiler_worker import CompilerWorker
from pixellang.compiler_service import Limits
from pixellang.vm import VM
from tests.native_compiler import NATIVE_COMPILER


class IncrementalIRTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.native_compiler import load_native_compiler
        cls.code = load_native_compiler(driver=None)

    def setUp(self):
        self.service=CompilerService(self.code,native_executable=NATIVE_COMPILER)
        self.addCleanup(self.service.close)

    def call(self,files,**options):
        operation=options.pop('operation','compile')
        options.setdefault('incremental',True)
        with ExitStack() as guards:
            for target in HOST_STAGES:
                guards.enter_context(patch(target,side_effect=AssertionError('Host frontend used')))
            return self.service.request(dict(version=1,operation=operation,files=files,
                                             reuseArtifacts=False,**options))

    def equal_fresh(self,files,expected,**options):
        cached=self.call(files,**options)
        fresh=self.call(files,incremental=False,**options)
        self.assertEqual(cached['status'],'ok',cached)
        self.assertEqual(fresh['status'],'ok',fresh)
        self.assertEqual(cached['result'],fresh['result'])
        self.assertEqual(cached['diagnostics'],fresh['diagnostics'])
        self.assertEqual(VM(cached['result']['bytecode']).run(),expected)
        return cached

    def warm(self,files):
        response=self.call(files)
        self.assertEqual(response['status'],'ok',response)
        self.assertGreater(response['metrics']['residentIRNodes'],0,response)

    def test_dependency_body_edit_reuses_caller(self):
        files={'main.pxl':'import "lib.pxl" as lib\neffect fn main(){print(lib.value())}',
               'lib.pxl':'export fn value()->int=42'}
        self.warm(files)
        files['lib.pxl']='export fn value()->int=63'
        result=self.equal_fresh(files,[63])
        self.assertGreater(result['metrics']['irCacheHits'],0)

    def test_same_file_body_edit_reuses_sibling_and_caller_ir(self):
        files={'main.pxl':'fn first()->int=40\nfn second()->int=2\neffect fn main(){print(first()+second())}'}
        self.warm(files)
        files['main.pxl']=files['main.pxl'].replace('=40','{let value=41;return value}')
        result=self.equal_fresh(files,[43],operation='debug-compile')
        self.assertEqual(result['metrics']['semanticCheckedFunctions'],1)
        self.assertEqual(result['metrics']['irCacheHits'],2)

    def test_comment_movement_rebases_every_reused_instruction(self):
        files={'main.pxl':'fn first()->int=40\nfn second()->int=2\neffect fn main(){print(first()+second())}'}
        self.warm(files)
        files['main.pxl']='// 雪 moves source positions\n\n'+files['main.pxl'].replace('=40','= 40')
        result=self.equal_fresh(files,[42],operation='debug-bundle',includeIR=True)
        self.assertEqual(result['metrics']['irCacheHits'],3)
        self.assertGreater(result['metrics']['irCacheRelocated'],0)

    def test_unrelated_same_file_declaration_does_not_invalidate_functions(self):
        files={'main.pxl':'fn value()->int=42\neffect fn main(){print(value())}'}
        self.warm(files)
        files['main.pxl']='fn unused()->string="unused"\n'+files['main.pxl']
        result=self.equal_fresh(files,[42],operation='debug-compile')
        self.assertEqual(result['metrics']['irCacheHits'],2)

    def test_closure_and_generic_ir_keep_stable_paths_after_preceding_edit(self):
        files={'main.pxl':'''fn preceding()->int=0
fn identity[T](value:T)->T=value
fn value()->int{let n=42;let get=fn()->int=n;return identity(get())}
effect fn main(){print(preceding());print(value())}'''}
        self.warm(files)
        files['main.pxl']=files['main.pxl'].replace('preceding()->int=0','preceding()->int{let n=[1,2];return len(n)}')
        result=self.equal_fresh(files,[2,42],operation='debug-bundle',includeIR=True)
        self.assertEqual(result['metrics']['irCacheHits'],4)

    def test_signature_change_invalidates_same_file_callers_before_reuse(self):
        files={'main.pxl':'fn value()->int=42\nfn use()->int=value()\neffect fn main(){print(use())}'}
        self.warm(files)
        files['main.pxl']=files['main.pxl'].replace('value()->int=42','value()->string="wrong"')
        cached=self.call(files)
        fresh=self.call(files,incremental=False)
        self.assertEqual(cached['status'],'diagnostics',cached)
        self.assertEqual(cached['diagnostics'],fresh['diagnostics'])

    def test_ir_reuse_does_not_consume_file_io_budget(self):
        self.service.close()
        self.service=CompilerService(self.code,native_executable=NATIVE_COMPILER,
                                     limits=Limits(file_bytes=1))
        self.addCleanup(self.service.close)
        files={'main.pxl':'effect fn main(){print(42)}'}
        self.warm(files)
        result=self.equal_fresh(files,[42])
        self.assertGreater(result['metrics']['irCacheHits'],0)

    def test_normalized_module_paths_can_reuse_native_ir(self):
        files={'main.pxl':'import "lib.pxl" as lib\neffect fn main(){print(lib.value())}',
               './lib.pxl':'export fn value()->int=42'}
        self.warm(files)
        result=self.equal_fresh(files,[42])
        self.assertGreater(result['metrics']['irCacheHits'],0)

    def test_declaration_position_changes_relocate_nominal_types(self):
        files={'main.pxl':'import "lib.pxl" as lib\neffect fn main(){let b=lib.Box{value:42};print(b.value)}',
               'lib.pxl':'fn before()->int=1\nexport record Box {value:int}'}
        self.warm(files)
        files['lib.pxl']='fn before()->int{let extra=[1,2,3];return len(extra)}\nexport record Box {value:int}'
        result=self.equal_fresh(files,[42])
        self.assertGreater(result['metrics']['irCacheRelocated'],0)

    def test_typed_capture_operand_relocates_nominal_identity(self):
        files = {
            'model.pxl': 'fn before()->int=1\nexport record Box {value:int}',
            'callback.pxl': 'import "model.pxl" as model\nexport fn make(value:model.Box)->fn()->int{return fn()=value.value}',
            'main.pxl': 'import "model.pxl" as model\nimport "callback.pxl" as callback\neffect fn main(){let f=callback.make(model.Box{value:42});print(f())}',
        }
        self.warm(files)
        files['model.pxl'] = 'fn before()->int{let extra=[1,2,3];return len(extra)}\nexport record Box {value:int}'
        result = self.equal_fresh(files, [42])
        self.assertGreater(result['metrics']['irCacheRelocated'], 0)

    def test_value_collection_optional_result_relocates_nominal_identity(self):
        files = {
            'model.pxl': 'fn before()->int=1\nexport record Box {value:int}',
            'operations.pxl': 'import "model.pxl" as model\nexport fn last(values:[model.Box])->Option[model.Box]{var local=values;return local.pop()}',
            'main.pxl': 'import "model.pxl" as model\nimport "operations.pxl" as operations\neffect fn main(){print(operations.last([model.Box{value:42}]))}',
        }
        self.warm(files)
        files['model.pxl'] = 'fn before()->int{let extra=[1,2,3];return len(extra)}\nexport record Box {value:int}'
        result = self.equal_fresh(files, [{'variant': 'Some', 'fields': {'value': {'value': 42}}}])
        self.assertGreater(result['metrics']['irCacheRelocated'], 0)

    def test_inout_access_and_signature_relocate_nominal_identity(self):
        files = {
            'model.pxl': 'fn before()->int=1\nexport record Box {value:int}',
            'operations.pxl': 'import "model.pxl" as model\nexport fn edit(value: inout model.Box){value.value+=1}',
            'main.pxl': 'import "model.pxl" as model\nimport "operations.pxl" as operations\neffect fn main(){var b=model.Box{value:41};operations.edit(&b);print(b.value)}',
        }
        self.warm(files)
        files['model.pxl'] = 'fn before()->int{let extra=[1,2,3];return len(extra)}\nexport record Box {value:int}'
        result = self.equal_fresh(files, [42])
        self.assertGreater(result['metrics']['irCacheRelocated'], 0)

    def test_new_calls_relocate_unchanged_function_targets(self):
        files={'main.pxl':'import "lib.pxl" as lib\neffect fn main(){print(lib.last())}',
               'lib.pxl':'export fn first()->int=1\nfn leaf()->int=42\nexport fn last()->int=leaf()'}
        self.warm(files)
        files['main.pxl']='import "lib.pxl" as lib\neffect fn main(){print(lib.first());print(lib.last())}'
        result=self.equal_fresh(files,[1,42])
        self.assertGreater(result['metrics']['irCacheRelocated'],0)

    def test_module_insertion_and_removal_preserve_debug_bundle(self):
        files={'main.pxl':'import "lib.pxl" as lib\neffect fn main(){print(lib.value())}',
               'lib.pxl':'export fn value()->int=42'}
        self.warm(files)
        files['extra.pxl']='export fn value()->int=1'
        files['main.pxl']='import "extra.pxl" as extra\nimport "lib.pxl" as lib\neffect fn main(){print(extra.value());print(lib.value())}'
        result=self.equal_fresh(files,[1,42],operation='debug-bundle',includeIR=True,includePNG=True)
        self.assertGreater(result['metrics']['irCacheRelocated'],0)
        del files['extra.pxl']
        files['main.pxl']='import "lib.pxl" as lib\neffect fn main(){print(lib.value())}'
        self.equal_fresh(files,[42],operation='debug-bundle',includeIR=True,includePNG=True)

    def test_unrelated_interface_edit_keeps_other_modules_reusable(self):
        files={'main.pxl':'import "a.pxl" as a\nimport "b.pxl" as b\neffect fn main(){print(a.value());print(b.value())}',
               'a.pxl':'export fn value()->int=1','b.pxl':'export fn value()->int=42'}
        self.warm(files)
        files['a.pxl']+='\nexport fn unused()->string="new"'
        result=self.equal_fresh(files,[1,42])
        self.assertGreater(result['metrics']['irCacheHits'],0)

    def test_generic_instances_and_captured_closures_remap_independently(self):
        files={'main.pxl':'import "lib.pxl" as lib\neffect fn main(){let f=lib.make(42);print(f())}',
               'lib.pxl':'export fn make[T](value:T)->fn()->T{return fn()=value}'}
        self.warm(files)
        files['main.pxl']='import "lib.pxl" as lib\neffect fn main(){let g=lib.make("x");let f=lib.make(42);print(g());print(f())}'
        result=self.equal_fresh(files,['x',42])
        self.assertGreater(result['metrics']['irCacheRelocated'],0)
        files['main.pxl']='import "lib.pxl" as lib\neffect fn main(){let f=lib.make(42);print(f())}'
        self.equal_fresh(files,[42])
        files['lib.pxl']='export fn make[T](value:T)->fn()->T{let other=value;return fn()=other}'
        self.equal_fresh(files,[42])

    def test_relocation_does_not_rewrite_type_shaped_string_literals(self):
        literal='@0:1[] fn(@1:2)->(@3:4)'
        files={'main.pxl':'import "lib.pxl" as lib\neffect fn main(){print(lib.text())}',
               'lib.pxl':f'export fn text()->string="{literal}"'}
        self.warm(files)
        files['extra.pxl']='export fn extra()->int=1'
        files['main.pxl']='import "extra.pxl" as extra\nimport "lib.pxl" as lib\neffect fn main(){print(extra.extra());print(lib.text())}'
        result=self.equal_fresh(files,[1,literal])
        self.assertGreater(result['metrics']['irCacheRelocated'],0)

    def test_error_then_fix_does_not_publish_partial_cache(self):
        files={'main.pxl':'import "lib.pxl" as lib\neffect fn main(){print(lib.value()+1)}',
               'lib.pxl':'export fn value()->int=41'}
        self.warm(files)
        previous=self.service.session_revision
        files['lib.pxl']='export fn value()->string="bad"'
        failure=self.call(files)
        self.assertEqual(failure['status'],'diagnostics',failure)
        self.assertEqual(failure['diagnostics'],self.call(files,incremental=False)['diagnostics'])
        self.assertEqual(self.service.session_revision,previous)
        files['lib.pxl']='export fn value()->int=42'
        self.equal_fresh(files,[43])

    def test_cancellation_discards_native_ir_snapshot(self):
        files={'main.pxl':'effect fn main(){print(42)}'}
        self.warm(files)
        process=self.service.native.process
        cancelled=False
        execute=self.service.native.execute
        def intercept(*args, **kwargs):
            nonlocal cancelled
            value=execute(*args, **kwargs)
            cancelled=True
            return value
        with patch.object(self.service.native,'execute',side_effect=intercept):
            result=self.service.request(dict(version=1,operation='compile',files=files),cancelled=lambda:cancelled)
        self.assertEqual(result['status'],'cancelled',result)
        self.assertIsNotNone(process.poll())
        self.assertEqual(result['metrics']['residentIRNodes'],0)
        self.assertEqual(self.equal_fresh(files,[42])['metrics']['irCacheHits'],0)

    def test_noncanonical_paths_use_full_lowering(self):
        files={'main.pxl':'import "lib.pxl" as lib\neffect fn main(){print(lib.value())}',
               './lib.pxl':'export fn value()->int=42'}
        first=self.equal_fresh(files,[42])
        self.assertEqual(first['metrics']['irCacheHits'],0)
        files['./lib.pxl']='export fn value()->string="changed"'
        result=self.equal_fresh(files,['changed'])
        self.assertEqual(result['metrics']['irCacheHits'],0)

    def test_native_flow_graph_relocates_with_its_cached_calls(self):
        files = {
            'main.pxl': 'import "lib.pxl" as lib\neffect fn main() { print(lib.Pipeline().step(3)) }',
            'lib.pxl': """fn Double(value: int) -> int = value * 2
export flow Pipeline() step(value: int) -> int {
 let doubled = Double(value)
 output doubled
}
""",
        }
        self.warm(files)
        files['extra.pxl'] = 'export fn first() -> int = 7'
        files['main.pxl'] = 'import "extra.pxl" as extra\nimport "lib.pxl" as lib\neffect fn main() { print(extra.first()); print(lib.Pipeline().step(3)) }'
        result = self.equal_fresh(files, [7, 6])
        self.assertGreater(result['metrics']['irCacheRelocated'], 0)
        self.assertEqual(len(result['result']['bytecode']['flows']), 1)

    def test_native_spatial_constructor_types_relocate_with_frames(self):
        files = {
            'main.pxl': 'import "lib.pxl" as lib\neffect fn main() { print(lib.value()) }',
            'lib.pxl': """frame World = meters
export effect fn value() -> float64 {
 let point = Point3::[World]{x: 0.0, y: 0.0, z: 4.0}
 let transform = Transform3::[World, World]{rotation: [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]], translation: Vec3::[World]{x: 0.0, y: 0.0, z: 2.0}}
 let space = Space::[World, int, string]("sample")
 return transform.point(point).z + float64(space.size())
}
""",
        }
        self.warm(files)
        files['extra.pxl'] = 'export fn first() -> int = 7'
        files['main.pxl'] = 'import "extra.pxl" as extra\nimport "lib.pxl" as lib\neffect fn main() { print(extra.first()); print(lib.value()) }'
        result = self.equal_fresh(files, [7, 6.0])
        self.assertGreater(result['metrics']['irCacheRelocated'], 0)


if __name__=='__main__':unittest.main()
