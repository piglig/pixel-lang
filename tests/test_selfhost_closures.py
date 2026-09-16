import json
import unittest
from pathlib import Path
from unittest.mock import patch

from pixellang.project import compile_project
from pixellang.vm import VM
from tests.selfhost_runtime import (SELFHOST_HEAP_LIMITS, SELFHOST_MAX_STEPS)

ROOT = Path(__file__).resolve().parents[1]


class SelfHostedClosureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = {p.name: p.read_text() for p in (ROOT / 'selfhost').glob('*.pxl')}
        cls.bytecode = compile_project(cls.files)[1].bytecode

    def plan(self, source):
        vm = VM(self.bytecode, **SELFHOST_HEAP_LIMITS, input_text=json.dumps({
            'source': 'main.pxl', 'text': '', 'operation': 'closures', 'files': {'main.pxl': source},
        }), max_steps=80_000_000, max_depth=1024)
        with patch('pixellang.text.parse_text', side_effect=AssertionError('delegated')), patch(
            'pixellang.semantics.Checker.check', side_effect=AssertionError('delegated')
        ):
            result = vm.run()[0]
        self.assertEqual(result['diagnostics'], [])
        return result

    def test_capture_types_and_mutability(self):
        result = self.plan('fn main() { var count = 0; let limit = 2; let increment = fn() -> int { count += limit; return count }; print(increment()) }')
        self.assertEqual(len(result['closures']), 1)
        captures = result['closures'][0]['captures']
        self.assertEqual([(result['symbols'][c['symbol']]['name'], c['mutable']) for c in captures], [('count', True), ('limit', False)])
        self.assertTrue(all(result['types'][c['typeId']]['name'] == 'int' for c in captures))

    def test_sibling_closures_share_binding_identity(self):
        result = self.plan('fn main() { var count = 0; let increment = fn() { count += 1 }; let read = fn() = count; increment(); print(read()) }')
        self.assertEqual(len(result['closures']), 2)
        left, right = [c['captures'][0] for c in result['closures']]
        self.assertEqual(left['symbol'], right['symbol'])
        self.assertTrue(left['mutable'] and right['mutable'])

    def test_nested_closure_parent_and_transitive_capture(self):
        result = self.plan('fn main() { var value = 1; let outer = fn() -> fn()->int { return fn() = value }; print(outer()()) }')
        closures = result['closures']
        self.assertEqual(len(closures), 2)
        root = next(i for i, c in enumerate(closures) if c['parent'] == -1)
        child = next(c for c in closures if c['parent'] >= 0)
        self.assertEqual(child['parent'], root)
        self.assertEqual(child['captures'][0]['symbol'], closures[root]['captures'][0]['symbol'])
        self.assertEqual(child['owner'], closures[root]['owner'])

    def test_generic_owner_produces_concrete_closure_instances(self):
        result = self.plan('fn make[T](value: T) -> fn()->T { return fn() = value }\nfn main() { let a = make(1); let b = make("x"); print(a()); print(b()) }')
        self.assertEqual(len(result['closures']), 2)
        returns = [result['types'][result['types'][c['typeId']]['arguments'][-1]]['name'] for c in result['closures']]
        self.assertEqual(returns, ['int', 'string'])
        self.assertEqual([result['types'][c['captures'][0]['typeId']]['name'] for c in result['closures']], ['int', 'string'])
        self.assertNotEqual(result['closures'][0]['owner'], result['closures'][1]['owner'])

    def test_unreachable_named_owner_does_not_emit_closures(self):
        result = self.plan('fn unused() { let f = fn() = 1 }\nfn main() {}')
        self.assertEqual(result['closures'], [])

    def test_iteration_binding_capture_keeps_its_symbol(self):
        result = self.plan('fn main() { let callbacks: [fn()->int] = []; for item in [1, 2] { append(callbacks, fn() = item) }; print(callbacks[0]()) }')
        capture = result['closures'][0]['captures'][0]
        self.assertEqual(result['symbols'][capture['symbol']]['name'], 'item')
        self.assertFalse(capture['mutable'])

    def test_compiler_pipeline_includes_closure_planning(self):
        vm = VM(self.bytecode, **SELFHOST_HEAP_LIMITS, input_text=json.dumps({
            'source': 'main.pxl', 'text': '', 'operation': 'closures-summary', 'files': self.files,
        }), max_steps=SELFHOST_MAX_STEPS, max_depth=1024)
        with patch('pixellang.text.parse_text', side_effect=AssertionError('delegated')), patch(
            'pixellang.semantics.Checker.check', side_effect=AssertionError('delegated')
        ):
            result = vm.run()[0]
        self.assertEqual(result['diagnostics'], [])
        self.assertGreater(result['instances'], 100)
