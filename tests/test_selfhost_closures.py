import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.native_compiler import compile_native_sources, execute_native_driver
from pixellang.vm import VM
from tests.selfhost_runtime import (SELFHOST_HEAP_LIMITS, SELFHOST_MAX_STEPS)

ROOT = Path(__file__).resolve().parents[1]


class SelfHostedClosureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = {p.name: p.read_text() for p in (ROOT / 'selfhost').glob('*.pxl')}
        cls.bytecode = compile_native_sources(cls.files)

    def plan(self, source):
        vm = VM(self.bytecode, **SELFHOST_HEAP_LIMITS, input_text=json.dumps({
            'source': 'main.pxl', 'text': '', 'operation': 'closures', 'files': {'main.pxl': source},
        }), max_steps=80_000_000, max_depth=1024)
        with patch('pixellang.text.parse_text', side_effect=AssertionError('delegated')):
            result = vm.run()[0]
        self.assertEqual(result['diagnostics'], [])
        return result

    def test_capture_types_and_mutability(self):
        result = self.plan('effect fn main() { var count = 0; let limit = 2; let increment = fn() -> int = count + limit; count += 1; print(increment()) }')
        self.assertEqual(len(result['closures']), 1)
        captures = result['closures'][0]['captures']
        self.assertEqual([(result['symbols'][c['symbol']]['name'], c['mutable']) for c in captures], [('count', False), ('limit', False)])
        self.assertTrue(all(result['types'][c['typeId']]['name'] == 'int' for c in captures))

    def test_sibling_value_captures_retain_source_identity(self):
        result = self.plan('effect fn main() { var count = 0; let first = fn() = count; count += 1; let read = fn() = count; print(first()); print(read()) }')
        self.assertEqual(len(result['closures']), 2)
        left, right = [c['captures'][0] for c in result['closures']]
        self.assertEqual(left['symbol'], right['symbol'])
        self.assertFalse(left['mutable'])
        self.assertFalse(right['mutable'])

    def test_nested_closure_parent_and_transitive_capture(self):
        result = self.plan('effect fn main() { var value = 1; let outer = fn() -> fn()->int { return fn() = value }; print(outer()()) }')
        closures = result['closures']
        self.assertEqual(len(closures), 2)
        root = next(i for i, c in enumerate(closures) if c['parent'] == -1)
        child = next(c for c in closures if c['parent'] >= 0)
        self.assertEqual(child['parent'], root)
        self.assertEqual(child['captures'][0]['symbol'], closures[root]['captures'][0]['symbol'])
        self.assertEqual(child['owner'], closures[root]['owner'])

    def test_generic_owner_produces_concrete_closure_instances(self):
        result = self.plan('fn make[T](value: T) -> fn()->T { return fn() = value }\neffect fn main() { let a = make(1); let b = make("x"); print(a()); print(b()) }')
        self.assertEqual(len(result['closures']), 2)
        returns = [result['types'][result['types'][c['typeId']]['arguments'][-1]]['name'] for c in result['closures']]
        self.assertEqual(returns, ['int', 'string'])
        self.assertEqual([result['types'][c['captures'][0]['typeId']]['name'] for c in result['closures']], ['int', 'string'])
        self.assertNotEqual(result['closures'][0]['owner'], result['closures'][1]['owner'])

    def test_unreachable_named_owner_does_not_emit_closures(self):
        result = self.plan('fn unused() { let f = fn() = 1 }\neffect fn main() {}')
        self.assertEqual(result['closures'], [])

    def test_iteration_binding_capture_keeps_its_symbol(self):
        result = self.plan('effect fn main() { var callbacks: [fn()->int] = []; for item in [1, 2] { callbacks.append(fn() = item) }; print(callbacks[0]()) }')
        capture = result['closures'][0]['captures'][0]
        self.assertEqual(result['symbols'][capture['symbol']]['name'], 'item')
        self.assertFalse(capture['mutable'])

    def test_compiler_pipeline_includes_closure_planning(self):
        request = dict(source='main.pxl', text='', operation='closures-summary', files=self.files)
        with patch('pixellang.text.parse_text', side_effect=AssertionError('delegated')):
            result = execute_native_driver(self.bytecode, request, steps=SELFHOST_MAX_STEPS)[0]
        self.assertEqual(result['diagnostics'], [])
        self.assertGreater(result['instances'], 100)

    def test_native_closures_match_reference(self):
        for source in ('fn main(){let x=1;let f=fn(y:int)=x+y;let value=f(2)}',
                       'fn main(){let value=missing}'):
            request=dict(source='main.pxl',text='',operation='closures',files={'main.pxl':source})
            with self.subTest(source=source):
                expected=VM(self.bytecode, **SELFHOST_HEAP_LIMITS, input_text=json.dumps(request),
                    max_steps=80_000_000,max_depth=1024).run()[0]
                self.assertEqual(execute_native_driver(self.bytecode,request,steps=80_000_000)[0],expected)
