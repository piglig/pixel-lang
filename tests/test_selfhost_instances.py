import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.native_compiler import compile_native_sources, execute_native_driver
from pixellang.vm import VM
from tests.selfhost_runtime import (SELFHOST_HEAP_LIMITS, SELFHOST_MAX_STEPS)

ROOT = Path(__file__).resolve().parents[1]


class SelfHostedInstanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = {p.name: p.read_text() for p in (ROOT / 'selfhost').glob('*.pxl')}
        cls.bytecode = compile_native_sources(cls.files)

    def plan(self, source):
        files = source if isinstance(source, dict) else {'main.pxl': source}
        vm = VM(self.bytecode, **SELFHOST_HEAP_LIMITS, input_text=json.dumps({
            'source': 'main.pxl', 'text': '', 'operation': 'instances', 'files': files,
        }), max_steps=80_000_000, max_depth=1024)
        with patch('pixellang.text.parse_text', side_effect=AssertionError('delegated')):
            return vm.run()[0]

    def names(self, result):
        return [result['symbols'][i['symbol']]['name'] for i in result['instances']]

    def test_only_reachable_named_functions_are_planned(self):
        result = self.plan('fn helper() {}\nfn unused() {}\neffect fn main() { helper() }')
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(self.names(result), ['main', 'helper'])
        self.assertEqual(result['entry'], 0)
        self.assertTrue(any(e['caller'] == 0 and e['target'] == 1 for e in result['edges']))

    def test_generic_instances_are_deduplicated(self):
        result = self.plan('fn identity[T](value: T) -> T = value\neffect fn main() { identity(1); identity(2); identity("x") }')
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(self.names(result), ['main', 'identity', 'identity'])
        instances = result['instances'][1:]
        self.assertEqual([result['types'][i['arguments'][0]]['name'] for i in instances], ['int', 'string'])

    def test_nested_named_function_dependencies_keep_containment_order(self):
        result = self.plan('fn helper() {}\neffect fn main() { fn unused() { helper() } }')
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(self.names(result), ['main', 'helper'])

    def test_recursive_generic_call_reuses_the_instance(self):
        result = self.plan('''fn repeat[T](count: int, value: T) -> T {
 if count == 0 { return value }
 return repeat(count - 1, value)
}
effect fn main() { print(repeat(3, "ok")) }
''')
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(self.names(result), ['main', 'repeat'])
        self.assertTrue(any(e['caller'] == 1 and e['target'] == 1 for e in result['edges']))

    def test_generic_arguments_propagate_across_calls(self):
        result = self.plan('fn inner[U](value: [U]) -> [U] = value\nfn outer[T](value: T) -> [T] = inner([value])\neffect fn main() { outer(1) }')
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(self.names(result), ['main', 'outer', 'inner'])
        for instance in result['instances'][1:]:
            self.assertEqual(result['types'][instance['arguments'][0]]['name'], 'int')

    def test_function_values_keep_their_code_reachable(self):
        result = self.plan('fn helper(value: int) -> int = value\nfn identity[T](value: T) -> T = value\neffect fn main() { let f = helper; let g = identity::[string]; print(f(1)); print(g("x")) }')
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(set(self.names(result)), {'main', 'helper', 'identity'})
        self.assertTrue(any(e['kind'] == 'value' for e in result['edges']))

    def test_entry_and_expanding_instantiation_errors(self):
        for source, expected in [
            ('fn helper() {}', 'must define main'),
            ('fn outer() { effect fn main() {} }', 'must define main'),
            ('fn main(value: int) {}', 'parameterless'),
            ('effect fn main() -> int = 1', 'return unit'),
            ('fn grow[T](value: T) { grow([value]) }\neffect fn main() { grow(1) }', 'depth 32'),
        ]:
            with self.subTest(source=source):
                result = self.plan(source)
                self.assertTrue(any(expected in e['message'] for e in result['diagnostics']))

    def test_imported_functions_keep_distinct_identities(self):
        result = self.plan({
            'main.pxl': 'import "a.pxl" as a\nimport "b.pxl" as b\neffect fn main() { a.Run(); b.Run() }',
            'a.pxl': 'export fn Run() {}', 'b.pxl': 'export fn Run() {}',
        })
        self.assertEqual(result['diagnostics'], [])
        runs = [i['symbol'] for i in result['instances'] if result['symbols'][i['symbol']]['name'] == 'Run']
        self.assertEqual(len(runs), 2)
        self.assertNotEqual(result['symbols'][runs[0]]['module'], result['symbols'][runs[1]]['module'])

    def test_generic_instance_count_is_bounded(self):
        records = '\n'.join(f'record R{i} {{ value: int }}' for i in range(17))
        calls = '\n'.join(f'pick(R{i // 17}{{value: 1}}, R{i % 17}{{value: 2}})' for i in range(257))
        result = self.plan(records + '\nfn pick[A, B](a: A, b: B) -> A = a\neffect fn main() {\n' + calls + '\n}')
        self.assertTrue(any('instances exceed 256' in e['message'] for e in result['diagnostics']))
        self.assertLessEqual(len(result['instances']), 257)

    def test_generic_values_preserve_array_and_callable_arguments(self):
        prefix = 'fn identity[T](value: T) -> T = value\n'
        for expression, kind in (('[1]', 'array'), ('fn() = 1', 'function')):
            with self.subTest(kind=kind):
                result = self.plan(prefix + 'fn main() { let copy = identity(' + expression + ') }')
                self.assertEqual(result['diagnostics'], [])
                instance = next(i for i in result['instances'] if result['symbols'][i['symbol']]['name'] == 'identity')
                self.assertEqual(result['types'][instance['arguments'][0]]['kind'], kind)

    def test_plans_its_own_compiler(self):
        result = execute_native_driver(self.bytecode, dict(source='main.pxl', text='',
            operation='instances-summary', files=self.files), steps=SELFHOST_MAX_STEPS)[0]
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(result['entry'], 0)
        self.assertGreater(result['instances'], 100)
        self.assertGreater(result['edges'], result['instances'])

    def test_native_instances_matches_reference(self):
        for source in ('fn identity[T](x:T)->T=x\neffect fn main(){print(identity(2))}',
                       'fn main(){missing()}'):
            with self.subTest(source=source):
                expected = self.plan(source)
                actual = execute_native_driver(self.bytecode, dict(source='main.pxl', text='',
                    operation='instances', files={'main.pxl': source}))[0]
                self.assertEqual(actual, expected)
