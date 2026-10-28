"""Incremental checks respect declared effects and recover after failed passes."""
import unittest

from tests.native_compiler import load_native_compiler, native_service


class NativeIncrementalEffectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code = load_native_compiler(driver=None)

    def setUp(self):
        self.service = native_service(self.code)
        self.addCleanup(self.service.close)

    def check(self, files, **options):
        return self.service.request(dict(version=1, operation='check', files=files, **options))

    def test_unchanged_value_builder_keeps_its_declared_effect_contract(self):
        files = {
            'lib.pxl': '''export fn Build() -> [int] {
 var values:[int] = []
 values.append(1)
 return values
}''',
            'main.pxl': '''import "lib.pxl" as lib
fn Value() -> int = len(lib.Build())
fn main() { Value() }''',
        }
        first = self.check(files)
        self.assertEqual(first['status'], 'ok', first)
        files['main.pxl'] = files['main.pxl'].replace('len(lib.Build())', 'len(lib.Build()) + 1')
        edited = self.check(files)
        self.assertEqual(edited['status'], 'ok', edited)
        self.assertEqual(edited['metrics']['semanticCheckedFunctions'], 1)
        self.assertEqual(edited['metrics']['semanticCacheHits'], 2)
        self.assertEqual(edited['diagnostics'], self.check(files, incremental=False)['diagnostics'])

    def test_fixing_a_type_error_runs_previously_skipped_effect_checks(self):
        files = {
            'lib.pxl': 'export fn Bad() { print(1) }',
            'main.pxl': '''import "lib.pxl" as lib
fn Value() -> int = "wrong"
fn main() { lib.Bad() }''',
        }
        invalid = self.check(files)
        self.assertEqual(invalid['status'], 'diagnostics', invalid)
        self.assertTrue(any(d['phase'] == 'check' for d in invalid['diagnostics']))
        # Failed checks must not publish successful semantic query snapshots.
        repeated = self.check(files)
        self.assertEqual(repeated['diagnostics'], invalid['diagnostics'])
        self.assertEqual(repeated['metrics']['sessionRevision'], 0)
        self.assertEqual(repeated['metrics']['semanticCacheHits'], 0)
        files['main.pxl'] = files['main.pxl'].replace('"wrong"', '0')
        edited = self.check(files)
        self.assertEqual(set(edited['result']['checkedModules']), set(files))
        self.assertTrue(any(d['phase'] == 'effect' and d['source'] == 'lib.pxl'
                            for d in edited['diagnostics']), edited)
        self.assertEqual(edited['diagnostics'], self.check(files, incremental=False)['diagnostics'])


PHASE_DRIVER = '''import "model.pxl" as model
import "project.pxl" as project
import "bindings.pxl" as bindings
import "types.pxl" as types
import "function_effects.pxl" as effects
record Request { files:map[string,string], selected:map[string,bool] }
effect fn main() {
 match jsonDecode::[Request](input()) { .Ok(request)=>{Dispatch(request)}, .Err(error)=>{print(error.message)} }
}
effect fn Dispatch(request:Request) {
 let resolved = types.Analyze(bindings.Bind(project.Build(request.files, "main.pxl")))
 assert(len(resolved.diagnostics) == 0)
 // Incremental callers have no body-expression facts for unselected modules.
 var references = map[string,int]{}
 for ref in resolved.bound.references { references[string(ref.module)+":"+string(ref.node)] = ref.symbol }
 print(effects.Analyze([], [], [], references, map[string,bool]{}, request.selected, false, resolved))
}
'''


class NativeEffectSubsetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        from tests.native_compiler import compile_native_sources
        root = Path(__file__).resolve().parents[1]
        names = ('model.pxl', 'lexer.pxl', 'parser.pxl', 'project.pxl',
                 'bindings.pxl', 'types.pxl', 'function_effects.pxl')
        files = {name: (root / 'selfhost' / name).read_text() for name in names}
        cls.code = compile_native_sources({**files, 'main.pxl': PHASE_DRIVER})

    def analyze(self, library, main):
        import json
        from pixellang.vm import VM
        return VM(self.code, input_text=json.dumps(dict(files={
            'lib.pxl': library, 'main.pxl': 'import "lib.pxl" as lib\n' + main,
        }, selected={'main.pxl': True})), max_steps=10_000_000).run()[0]

    def test_unselected_value_methods_do_not_create_unknown_effects(self):
        self.assertEqual(self.analyze('''export fn Build() -> [int] {
 var values:[int] = []
 values.append(1)
 return values
}''', 'fn main() { lib.Build() }'), [])

    def test_unselected_explicit_effect_contract_is_still_enforced(self):
        diagnostics = self.analyze('export effect fn Trace() {}', 'fn main() { lib.Trace() }')
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0]['source'], 'main.pxl')
        self.assertEqual(diagnostics[0]['phase'], 'effect')
        self.assertIn('effect fn', diagnostics[0]['message'])


if __name__ == '__main__':
    unittest.main()
