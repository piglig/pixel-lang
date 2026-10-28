"""Function checking reuse must agree with a fresh compiler after every edit."""
import unittest
from pixellang.vm import VM
from tests.native_compiler import native_service, load_native_compiler


class SemanticIncrementalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code = load_native_compiler(driver=None)

    def setUp(self):
        self.service = native_service(self.code)
        self.addCleanup(self.service.close)

    def compile(self, files, **options):
        return self.service.request(dict(version=1, operation='debug-compile', files=files,
            reuseArtifacts=False, **options))

    def warm(self, files):
        result = self.compile(files)
        self.assertEqual(result['status'], 'ok', result)
        self.assertGreater(result['metrics']['residentCacheNodes'], 0)
        return result

    def equivalent(self, files, output=None):
        reused = self.compile(files)
        fresh = self.compile(files, incremental=False)
        self.assertEqual(reused['status'], fresh['status'], reused)
        self.assertEqual(reused['diagnostics'], fresh['diagnostics'])
        self.assertEqual(reused.get('result'), fresh.get('result'))
        if output is not None:
            self.assertEqual(reused['status'], 'ok', reused)
            self.assertEqual(VM(reused['result']['bytecode']).run(), output)
        return reused

    def test_structural_queries_distinguish_escaped_literal_edits(self):
        import json
        values = ['a,b', 'a", "b', '雪\n\t\\"', '']
        for value in values:
            files = {'main.pxl': 'fn Value()->string=' + json.dumps(value, ensure_ascii=False)
                     + '\neffect fn main(){print(Value())}'}
            changed = self.equivalent(files, [value])
            self.assertEqual(changed['metrics']['semanticCheckedFunctions'], 2 if value == values[0] else 1)
            warm = self.equivalent(files, [value])
            self.assertEqual(warm['metrics']['semanticCheckedFunctions'], 0)
            self.assertEqual(warm['metrics']['semanticCacheHits'], 2)

    def test_full_rebuild_uses_temporary_process_and_preserves_warm_session(self):
        from unittest.mock import patch
        from pixellang.native_session import NativeSessionExecution
        files = {'main.pxl': 'fn Value()->int=42\neffect fn main(){print(Value())}'}
        self.warm(files)
        warm = self.compile(files)
        process = self.service.native.process
        isolated = []
        def create(executable):
            lane = NativeSessionExecution(executable)
            isolated.append(lane)
            return lane
        with patch('pixellang.native_session.NativeSessionExecution', side_effect=create):
            full = self.compile(files, incremental=False)
            from dataclasses import replace
            with patch.object(self.service, 'limits', replace(self.service.limits, steps=1)):
                exhausted = self.compile(files, incremental=False)
            self.assertEqual(exhausted['status'], 'error', exhausted)
            self.assertEqual(exhausted['error']['code'], 'service.steps')
        self.assertEqual(full['status'], 'ok', full)
        self.assertEqual(full['result'], warm['result'])
        self.assertEqual(full['metrics']['semanticCacheHits'], 0)
        self.assertTrue(isolated)
        self.assertTrue(all(lane.process is None for lane in isolated))
        self.assertIs(self.service.native.process, process)
        self.assertIsNone(process.poll())
        again = self.compile(files)
        self.assertGreater(again['metrics']['semanticCacheHits'], 0)
        self.assertGreater(again['metrics']['irCacheHits'], 0)

    def test_declaration_metrics_reset_for_artifact_hits_full_builds_and_rejections(self):
        files = {'main.pxl': 'fn Value()->int=42\neffect fn main(){print(Value())}'}
        names = ('bindingCacheHits', 'bindingCheckedDeclarations', 'typeCacheHits', 'typeCheckedDeclarations')
        def counts(response):
            return tuple(response['metrics'][name] for name in names)
        cold = self.warm(files)
        self.assertEqual(counts(cold), (0, 2, 0, 2))
        self.assertEqual(counts(self.compile(files)), (2, 0, 2, 0))
        request = dict(version=1, operation='debug-compile', files=files, reuseArtifacts=True)
        built = self.service.request(request)
        cached = self.service.request(request)
        self.assertEqual(built['status'], 'ok', built)
        self.assertTrue(cached['metrics']['artifactCacheHit'])
        self.assertEqual(counts(cached), (0, 0, 0, 0))
        self.assertEqual(counts(self.compile(files, incremental=False)), (0, 0, 0, 0))
        rejected = self.service.request(dict(version=1, operation='unknown'))
        self.assertEqual(rejected['status'], 'error')
        self.assertEqual(counts(rejected), (0, 0, 0, 0))
        self.assertEqual(counts(self.compile(files)), (2, 0, 2, 0))

    def test_declaration_type_queries_reuse_relocate_and_validate_changed_schemas(self):
        source = 'record Box[T:Equatable]{value:T}\nrecord Item{value:int}\nfn Unused(value:Box[Item]){}\nfn Value()->int=1\neffect fn main(){print(Value())}'
        files = {'main.pxl': source}
        self.warm(files)
        response = self.compile(files)
        self.assertEqual((response['metrics']['typeCacheHits'], response['metrics']['typeCheckedDeclarations']), (5, 0))
        files['main.pxl'] = source.replace('Value()->int=1', 'Value()->int=2')
        response = self.compile(files)
        self.assertEqual(response['status'], 'ok', response)
        self.assertEqual((response['metrics']['typeCacheHits'], response['metrics']['typeCheckedDeclarations']), (4, 1))
        self.equivalent(files, [2])
        files['main.pxl'] = 'fn Earlier()->int=0\n' + files['main.pxl']
        self.equivalent(files, [2])
        files['main.pxl'] = files['main.pxl'].replace('record Item{value:int}', 'record Item{value:fn(int)->int}')
        rejected = self.equivalent(files)
        self.assertEqual(rejected['status'], 'diagnostics')
        self.assertIn('generic capability', str(rejected))
        files['main.pxl'] = source
        self.equivalent(files, [1])

    def test_type_name_environment_tracks_import_targets_visibility_and_kind(self):
        files = {
            'a.pxl': 'export record Item { value: int }',
            'b.pxl': 'export record Item { value: string }',
            'main.pxl': 'import "a.pxl" as selected\nimport "a.pxl" as keep\nimport "b.pxl" as other\nfn Make()->selected.Item=selected.Item{value:1}\neffect fn main(){print(Make().value)}'}
        self.warm(files)
        self.equivalent(files, [1])
        files['main.pxl'] = files['main.pxl'].replace('"a.pxl" as selected', '"b.pxl" as selected')
        self.assertEqual(self.equivalent(files)['status'], 'diagnostics')
        files['main.pxl'] = files['main.pxl'].replace('value:1', 'value:"ready"')
        self.equivalent(files, ['ready'])
        files['b.pxl'] = 'record Item { value: string }'
        self.assertEqual(self.equivalent(files)['status'], 'diagnostics')
        files['b.pxl'] = 'export fn Item()->int=1'
        self.assertEqual(self.equivalent(files)['status'], 'diagnostics')
        files['b.pxl'] = 'export record Item { value: string }'
        self.equivalent(files, ['ready'])

    def test_binding_queries_relocate_closure_scopes_without_rebinding_unchanged_declarations(self):
        files = {'lib.pxl':'export fn plus(x:int)->int=x+1',
                 'main.pxl': 'import "lib.pxl" as lib\nfn read()->int {let base=2;let f=fn(x:int)->int=lib.plus(base+x);return f(0)}\neffect fn main(){print(read())}'}
        self.warm(files)
        files['main.pxl'] = 'fn Unused()->int=0\n' + files['main.pxl']
        result = self.compile(files)
        self.assertEqual(result['status'], 'ok', result)
        self.assertEqual(VM(result['result']['bytecode']).run(), [3])
        self.assertEqual(result['metrics']['bindingCacheHits'], 3)
        self.assertEqual(result['metrics']['bindingCheckedDeclarations'], 1)
        self.equivalent(files, [3])
        files['lib.pxl'] = files['lib.pxl'].replace('export ', '')
        self.assertEqual(self.equivalent(files)['status'], 'diagnostics')
        files['lib.pxl'] = 'export fn plus(x:int)->int=x+1'
        self.equivalent(files, [3])

    def test_binding_query_invalidates_builtin_shadowing_and_import_retargeting(self):
        original = 'effect fn main(){print(len([1,2]))}'
        self.warm({'main.pxl':original})
        self.equivalent({'main.pxl':'fn len(values:[int])->int=7\n'+original}, [7])
        self.equivalent({'main.pxl':original}, [2])
        files = {'a.pxl':'export fn value()->int=1', 'b.pxl':'export fn value()->int=2',
                 'main.pxl':'import "a.pxl" as selected\nimport "a.pxl" as keep\nimport "b.pxl" as other\neffect fn main(){print(selected.value())}'}
        self.warm(files)
        files['main.pxl'] = files['main.pxl'].replace('"a.pxl" as selected','"b.pxl" as selected')
        self.equivalent(files, [2])

    def test_flow_queries_reuse_and_relocate_across_unrelated_function_edits(self):
        files = {'lib.pxl': 'export fn Add(a:int,b:int)->int=a+b',
                 'main.pxl': """import "lib.pxl" as lib
record Config { initial: int }
flow Child(config: Config) step(x:int)->int {
 state value=config.initial
 next value=lib.Add(value,x)
 output value
}
flow Parent(config:Config) step(x:int)->int {
 instance child=Child(config)
 output child.step(x)+config.initial
}
effect fn main(){let f=Parent(Config{initial:10});print(f.step(1));print(f.step(2))}
"""}
        cold = self.warm(files)
        warm = self.equivalent(files, [20,21])
        self.assertEqual(warm['metrics']['semanticCheckedFunctions'], 0)
        self.assertEqual(warm['metrics']['semanticCacheHits'], 4)
        self.assertEqual(warm['metrics']['irCacheHits'], 4)
        files['lib.pxl'] = files['lib.pxl'].replace('a+b', 'a+b+1')
        changed = self.equivalent(files, [20,22])
        self.assertEqual(changed['metrics']['semanticCheckedFunctions'], 1)
        self.assertEqual(changed['metrics']['semanticCacheHits'], 3)
        self.assertEqual(changed['metrics']['irCacheHits'], 3)
        files['main.pxl'] = 'record Unrelated { text:string }\nfn Earlier()->int=0\n' + files['main.pxl']
        relocated = self.equivalent(files, [20,22])
        self.assertGreaterEqual(relocated['metrics']['semanticCacheHits'], 3)
        self.equivalent(files, [20,22])

    def test_flow_ir_relocates_child_initializers_nominals_and_closures_together(self):
        source = """record Box { value:int }
flow Child(initial:int) step(x:int)->int {
 state box=Box{value:initial}
 let read=fn()->int=box.value
 next box=box with {value:x}
 output read()
}
flow Parent(initial:int) step(x:int)->int {
 instance first=Child(initial+1)
 instance second=Child(initial+10)
 let value=first.step(x)
 output second.step(value)
}
effect fn main(){let f=Parent(3);print(f.step(5));print(f.step(7))}
"""
        self.warm({'main.pxl':source})
        shifted = 'record Earlier { text:string }\nfn Before()->int=0\n' + source
        result = self.equivalent({'main.pxl':shifted}, [13,4])
        self.assertGreaterEqual(result['metrics']['irCacheHits'], 3)
        self.equivalent({'main.pxl':shifted}, [13,4])
        self.equivalent({'main.pxl':shifted.replace('initial+10','initial+20')}, [23,4])

    def test_flow_dependency_edits_and_failed_checks_do_not_reuse_invalid_plans(self):
        source = """record Config { initial:int }
flow Child(config:Config) step(x:int)->int { state value=config.initial;next value=x;output value }
flow Parent(config:Config) step(x:int)->int { instance child=Child(config);output child.step(x) }
effect fn main(){let f=Parent(Config{initial:3});print(f.step(7));print(f.step(8))}
"""
        self.warm({'main.pxl':source})
        changed = source.replace('next value=x', 'next value=x+1')
        result = self.equivalent({'main.pxl':changed}, [3,8])
        self.assertGreaterEqual(result['metrics']['semanticCheckedFunctions'], 2)
        invalid = changed.replace('output child.step(x)', 'output child.step(x)+child.step(x)')
        result = self.equivalent({'main.pxl':invalid})
        self.assertEqual(result['status'], 'diagnostics')
        self.equivalent({'main.pxl':changed}, [3,8])
        invalid = changed.replace('initial:int', 'initial:string')
        result = self.equivalent({'main.pxl':invalid})
        self.assertEqual(result['status'], 'diagnostics')
        self.equivalent({'main.pxl':changed}, [3,8])

    def test_sibling_body_change_checks_one_function(self):
        files = {'main.pxl':'fn value()->int=40\nfn sibling()->int=2\neffect fn main(){print(value()+sibling())}'}
        self.warm(files)
        files['main.pxl'] = files['main.pxl'].replace('=40', '{ let x=41; return x }')
        result = self.equivalent(files, [43])
        self.assertEqual(result['metrics']['semanticCheckedFunctions'], 1)
        self.assertEqual(result['metrics']['semanticCacheHits'], 2)

    def test_callee_body_change_keeps_caller(self):
        files={'lib.pxl':'export fn value()->int=42', 'main.pxl':'import "lib.pxl" as lib\neffect fn main(){print(lib.value())}'}
        self.warm(files)
        files['lib.pxl']='export fn value()->int=63'
        result=self.equivalent(files,[63])
        self.assertEqual(result['metrics']['semanticCacheHits'],1)
        self.assertEqual(result['metrics']['semanticCheckedFunctions'],1)

    def test_signature_change_invalidates_caller(self):
        files={'lib.pxl':'export fn value()->int=42', 'main.pxl':'import "lib.pxl" as lib\nfn use()->int=lib.value()\neffect fn main(){print(use())}'}
        self.warm(files)
        files['lib.pxl']='export fn value()->string="bad"'
        result=self.equivalent(files)
        self.assertEqual(result['status'],'diagnostics')

    def test_nominal_schema_change_invalidates_user(self):
        files={'lib.pxl':'export record Box{value:int}', 'main.pxl':'import "lib.pxl" as lib\neffect fn main(){let b=lib.Box{value:42};print(b.value)}'}
        self.warm(files)
        files['lib.pxl']='export record Box{value:string}'
        self.assertEqual(self.equivalent(files)['status'],'diagnostics')

    def test_nominal_and_symbol_indices_relocate(self):
        files={'main.pxl':'record Box{value:int}\nfn value()->Box=Box{value:42}\neffect fn main(){print(value().value)}'}
        self.warm(files)
        files['main.pxl']='record Added{other:string}\nfn preceding()->int=1\n'+files['main.pxl']
        result=self.equivalent(files,[42])
        self.assertEqual(result['metrics']['semanticCacheHits'],2)

    def test_module_order_changes_relocate(self):
        files={'a.pxl':'export fn a()->int=40','b.pxl':'export record Box{value:int}\nexport fn b()->Box=Box{value:2}',
               'main.pxl':'import "a.pxl" as a\nimport "b.pxl" as b\neffect fn main(){print(a.a()+b.b().value)}'}
        self.warm(files)
        files['main.pxl']=files['main.pxl'].replace('import "a.pxl" as a\nimport "b.pxl" as b','import "b.pxl" as b\nimport "a.pxl" as a')
        self.assertGreater(self.equivalent(files,[42])['metrics']['semanticCacheHits'],0)

    def test_generic_and_closure_results_relocate(self):
        files={'main.pxl':'record Box[T]{value:T}\nfn identity[T](v:T)->T=v\nfn run()->int{let x=Box::[int]{value:42};let f=fn()->int=x.value;return identity(f())}\neffect fn main(){print(run())}'}
        self.warm(files)
        files['main.pxl']='fn prefix()->int=1\n'+files['main.pxl']
        self.assertGreater(self.equivalent(files,[42])['metrics']['semanticCacheHits'],0)

    def test_transitive_effect_error_is_not_hidden(self):
        files={'main.pxl':'fn leaf()->int=42\nfn middle()->int=leaf()\neffect fn main(){print(middle())}'}
        self.warm(files)
        files['main.pxl']=files['main.pxl'].replace('fn leaf()->int=42','fn leaf()->int{print(0);return 42}')
        result=self.equivalent(files)
        self.assertEqual(result['status'],'diagnostics')
        self.assertTrue(any(d['phase']=='effect' for d in result['diagnostics']))

    def test_effect_violation_is_local_to_the_definition(self):
        files={'main.pxl':'fn leaf()->int=42\nfn middle()->int=leaf()\neffect fn main(){print(middle())}'}
        self.warm(files)
        files['main.pxl']=files['main.pxl'].replace('fn leaf()->int=42','fn leaf()->int{print(0);return 42}')
        result=self.equivalent(files)
        effects=[d for d in result['diagnostics'] if d['phase']=='effect']
        self.assertEqual(len(effects),1,result)
        self.assertEqual(effects[0]['line'],1)
        self.assertEqual(result['metrics']['semanticCheckedFunctions'],1)
        self.assertEqual(result['metrics']['semanticCacheHits'],2)

    def test_effect_contract_change_rechecks_cached_alias_callers(self):
        files={'main.pxl':'fn operation(){}\nfn use(){let alias=operation;alias()}\neffect fn main(){use()}'}
        self.warm(files)
        files['main.pxl']=files['main.pxl'].replace('fn operation(){}','effect fn operation(){}')
        result=self.equivalent(files)
        self.assertEqual(result['status'],'diagnostics',result)
        self.assertTrue(any(d['phase']=='effect' and d['line']==2 for d in result['diagnostics']))

    def test_widened_immutable_callable_uses_its_declared_effect_contract(self):
        for value in ('operation','fn(){}'):
            with self.subTest(value=value):
                files={'main.pxl':'fn operation(){}\nfn use(){let alias:effect fn()='+value+';alias()}\neffect fn main(){use()}'}
                result=self.equivalent(files)
                self.assertEqual(result['status'],'diagnostics',result)
                self.assertTrue(any(d['phase']=='effect' for d in result['diagnostics']))

    def test_error_recovery_does_not_publish_partial_cache(self):
        files={'main.pxl':'fn value()->int=42\neffect fn main(){print(value())}'}
        self.warm(files)
        previous=self.service.session_revision
        files['main.pxl']=files['main.pxl'].replace('=42','=missing')
        self.assertEqual(self.equivalent(files)['status'],'diagnostics')
        self.assertEqual(self.service.session_revision,previous)
        files['main.pxl']=files['main.pxl'].replace('=missing','=43')
        self.equivalent(files,[43])

    def test_comment_moves_debug_positions(self):
        files={'main.pxl':'fn value()->int=42\neffect fn main(){print(value())}'}
        self.warm(files)
        files['main.pxl']='// new line\n\n'+files['main.pxl']
        self.assertEqual(self.equivalent(files,[42])['metrics']['semanticCacheHits'],2)

    def test_changed_generic_constraint_invalidates_call(self):
        files={'lib.pxl':'export fn identity[T](value:T)->T=value',
               'main.pxl':'import "lib.pxl" as lib\neffect fn main(){print(lib.identity(true))}'}
        self.warm(files)
        files['lib.pxl']=files['lib.pxl'].replace('[T]', '[T:Numeric]')
        self.assertEqual(self.equivalent(files)['status'],'diagnostics')

    def test_deleted_binding_is_not_hidden(self):
        files={'main.pxl':'fn value()->int=42\neffect fn main(){print(value())}'}
        self.warm(files)
        files['main.pxl']='effect fn main(){print(value())}'
        self.assertEqual(self.equivalent(files)['status'],'diagnostics')

    def test_visibility_change_is_not_hidden(self):
        files={'lib.pxl':'export fn value()->int=42',
               'main.pxl':'import "lib.pxl" as lib\neffect fn main(){print(lib.value())}'}
        self.warm(files)
        files['lib.pxl']='fn value()->int=42'
        self.assertEqual(self.equivalent(files)['status'],'diagnostics')

    def test_inout_call_and_locals_relocate(self):
        files={'main.pxl':'fn add(value:inout int){value+=1}\nfn result()->int{var n=41;add(&n);return n}\neffect fn main(){print(result())}'}
        self.warm(files)
        files['main.pxl']='fn prefix()->int=0\n'+files['main.pxl']
        self.assertEqual(self.equivalent(files,[42])['metrics']['semanticCacheHits'],3)

    def test_recursion_implementation_edit_keeps_caller(self):
        files={'main.pxl':'fn count(n:int)->int{if n==0{return 0};return count(n-1)+1}\neffect fn main(){print(count(3))}'}
        self.warm(files)
        files['main.pxl']=files['main.pxl'].replace('count(n-1)+1','count(n-1)+2')
        self.assertEqual(self.equivalent(files,[6])['metrics']['semanticCacheHits'],1)

    def test_cancelled_response_discards_session_and_next_request_rebuilds(self):
        from unittest.mock import patch
        files={'main.pxl':'fn value()->int=42\neffect fn main(){print(value())}'}
        self.warm(files)
        process=self.service.native.process
        files['main.pxl']=files['main.pxl'].replace('=42','=43')
        cancelled=False
        execute=self.service.native.execute
        def cancel_at_publication(*args,**kwargs):
            nonlocal cancelled
            result=execute(*args,**kwargs)
            cancelled=True
            return result
        with patch.object(self.service.native,'execute',side_effect=cancel_at_publication):
            result=self.service.request(dict(version=1,operation='compile',files=files),cancelled=lambda:cancelled)
        self.assertEqual(result['status'],'cancelled')
        self.assertIsNone(self.service.native.process)
        self.assertIsNotNone(process.poll())
        self.assertEqual(self.service.session_revision,0)
        self.assertEqual(self.equivalent(files,[43])['metrics']['semanticCacheHits'],0)

    def test_terminated_session_rebuilds_without_stale_results(self):
        files={'main.pxl':'fn value()->int=42\neffect fn main(){print(value())}'}
        self.warm(files)
        self.service.native.process.kill()
        self.service.native.process.wait()
        files['main.pxl']='// changed position\n'+files['main.pxl']
        self.assertEqual(self.equivalent(files,[42])['metrics']['semanticCacheHits'],0)

    def test_small_io_budget_does_not_prevent_memory_cache(self):
        from pixellang.compiler_service import Limits
        self.service.limits=Limits(file_bytes=1)
        files={'main.pxl':'effect fn main(){print(42)}'}
        result=self.compile(files)
        self.assertEqual(result['status'],'ok',result)
        self.assertGreater(result['metrics']['residentCacheNodes'],0)
        self.assertEqual(self.equivalent(files,[42])['metrics']['semanticCacheHits'],1)
