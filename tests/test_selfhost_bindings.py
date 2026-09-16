import json
import unittest
from pathlib import Path
from unittest.mock import patch

from pixellang.project import compile_project
from pixellang.vm import VM
from tests.selfhost_runtime import SELFHOST_HEAP_LIMITS

ROOT = Path(__file__).resolve().parents[1]


class SelfHostedBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = {p.name: p.read_text() for p in (ROOT / 'selfhost').glob('*.pxl')}
        cls.bytecode = compile_project(cls.files)[1].bytecode

    def bind(self, files):
        if isinstance(files, str):
            files = {'main.pxl': files}
        vm = VM(self.bytecode, **SELFHOST_HEAP_LIMITS, input_text=json.dumps({
            'source': 'main.pxl', 'text': '', 'operation': 'bind', 'files': files,
        }), max_steps=120_000_000, max_depth=1024, max_output_chars=64_000_000)
        with patch('pixellang.project.build_project', side_effect=AssertionError('delegated')), patch(
            'pixellang.text.parse_text', side_effect=AssertionError('delegated')
        ), patch('pixellang.semantics.Checker.check', side_effect=AssertionError('delegated')):
            return vm.run()[0]

    def test_binds_own_compiler(self):
        result = self.bind(self.files)
        self.assertEqual(result['diagnostics'], [])
        self.assertGreater(len(result['references']), 1000)
        for ref in result['references']:
            self.assertGreaterEqual(ref['symbol'], 0)
            self.assertLess(ref['symbol'], len(result['symbols']))

    def test_lexical_shadowing_binds_distinct_symbols(self):
        source = '''fn main() {
 let value = 1
 if true { let value = 2; print(value) }
 print(value)
}
'''
        result = self.bind(source)
        self.assertEqual(result['diagnostics'], [])
        values = [i for i, symbol in enumerate(result['symbols']) if symbol['name'] == 'value']
        self.assertEqual(len(values), 2)
        uses = [r['symbol'] for r in result['references'] if r['symbol'] in values]
        self.assertEqual(uses, [values[1], values[0]])

    def test_capture_propagates_through_nested_closures(self):
        source = '''fn main() {
 var count = 0
 let outer = fn() {
  let inner = fn() { count += 1; print(count) }
  inner()
 }
 outer()
}
'''
        result = self.bind(source)
        self.assertEqual(result['diagnostics'], [])
        count = next(i for i, s in enumerate(result['symbols']) if s['name'] == 'count')
        captures = [c for c in result['captures'] if c['symbol'] == count]
        self.assertEqual(len(captures), 2)
        self.assertEqual(len({c['function'] for c in captures}), 2)

    def test_unknown_duplicate_immutable_and_scope_escape(self):
        for source, message in [
            ('fn main() { print(missing) }', 'Unknown name: missing'),
            ('fn main() { let value = value }', 'Unknown name: value'),
            ('fn main() { let value = 1; let value = 2 }', 'Duplicate local name: value'),
            ('fn main() { let value = 1; value = 2 }', 'Cannot rebind immutable name: value'),
            ('fn main() { for item in [1] { print(item) }; print(item) }', 'Unknown name: item'),
            ('fn main() { try { print(1) } catch error { print(error) }; print(error) }', 'Unknown name: error'),
            ('fn main() { let value = match Some(1) { Some(item) => item, None => 0 }; print(item) }', 'Unknown name: item'),
        ]:
            with self.subTest(source=source):
                errors = self.bind(source)['diagnostics']
                self.assertEqual(len(errors), 1)
                self.assertEqual(errors[0]['phase'], 'bind')
                self.assertEqual(errors[0]['message'], message)

    def test_module_members_respect_visibility(self):
        for member, expected in [('Public', None), ('Private', 'private'), ('Missing', 'Unknown module member')]:
            result = self.bind({
                'main.pxl': f'import "lib.pxl" as lib\nfn main() {{ lib.{member}() }}',
                'lib.pxl': 'export fn Public() {}\nfn Private() {}',
            })
            if expected is None:
                self.assertEqual(result['diagnostics'], [])
                symbol = next(i for i, s in enumerate(result['symbols']) if s['name'] == member)
                self.assertTrue(any(r['symbol'] == symbol for r in result['references']))
            else:
                self.assertIn(expected, result['diagnostics'][0]['message'])

    def test_record_shorthand_is_a_name_use(self):
        result = self.bind('record R { value: int }\nfn main() { let result = R{value} }')
        self.assertEqual(result['diagnostics'][0]['message'], 'Unknown name: value')

    def test_real_project_bindings(self):
        library = {
            'std/' + p.name: p.read_text()
            for p in (ROOT / 'pixellang' / 'stdlib').glob('*.pxl')
        }
        for name in ('ledger', 'log-analysis', 'callback-analysis'):
            with self.subTest(project=name):
                files = {
                    p.name: p.read_text()
                    for p in (ROOT / 'examples' / name).glob('*.pxl')
                }
                files.update(library)
                result = self.bind(files)
                self.assertEqual(result['diagnostics'], [])

    def test_member_mutation_does_not_rebind_immutable_container(self):
        result = self.bind('record R { count: int }\nfn main() { let r = R{count: 0}; r.count += 1; let a = [1]; a[0] = 2 }')
        self.assertEqual(result['diagnostics'], [])

    def test_loop_source_cannot_see_its_binding(self):
        result = self.bind('fn main() { for item in item { print(item) } }')
        self.assertEqual(result['diagnostics'][0]['message'], 'Unknown name: item')
