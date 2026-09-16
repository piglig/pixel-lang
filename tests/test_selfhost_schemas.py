import json
import unittest
from pathlib import Path
from unittest.mock import patch

from pixellang.project import compile_project
from pixellang.vm import VM
from tests.selfhost_runtime import (SELFHOST_HEAP_LIMITS, SELFHOST_MAX_STEPS)

ROOT = Path(__file__).resolve().parents[1]


class SelfHostedSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = {p.name: p.read_text() for p in (ROOT / 'selfhost').glob('*.pxl')}
        cls.bytecode = compile_project(cls.files)[1].bytecode

    def plan(self, files, summary=False):
        if isinstance(files, str):
            files = {'main.pxl': files}
        vm = VM(self.bytecode, **SELFHOST_HEAP_LIMITS, input_text=json.dumps({
            'source': 'main.pxl', 'text': '',
            'operation': 'schemas-summary' if summary else 'schemas', 'files': files,
        }), max_steps=SELFHOST_MAX_STEPS, max_depth=1024)
        with patch('pixellang.text.parse_text', side_effect=AssertionError('delegated')), patch(
            'pixellang.semantics.Checker.check', side_effect=AssertionError('delegated')
        ):
            return vm.run()[0]

    def test_generic_fields_and_enum_payloads(self):
        result = self.plan('''record Box[T] { value: T }
enum Choice[T] { Value(item: Box[T]), Empty }
fn make[T](value: T) -> Choice[T] { return Choice[T].Value(Box[T]{value}) }
fn main() { print(make(1)); print(make("x")) }''')
        self.assertEqual(result['diagnostics'], [])
        arena = result['types']
        boxes = [s for s in result['schemas'] if arena[s['typeId']]['name'] == 'Box']
        self.assertEqual({arena[s['fields'][0]['typeId']]['name'] for s in boxes}, {'int', 'string'})
        choices = [s for s in result['schemas'] if arena[s['typeId']]['name'] == 'Choice']
        self.assertEqual(len(choices), 2)
        box_ids = {s['typeId'] for s in boxes}
        for schema in choices:
            self.assertEqual([v['name'] for v in schema['variants']], ['Value', 'Empty'])
            self.assertIn(schema['variants'][0]['fields'][0]['typeId'], box_ids)
            self.assertEqual(schema['variants'][1]['fields'], [])

    def test_recursive_schema_terminates_and_preserves_identity(self):
        result = self.plan('record Link { next: Option[Link] }\nfn main() { let node = Link{next: None}; print(node) }')
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(len(result['schemas']), 2)
        arena = result['types']
        link = next(s for s in result['schemas'] if arena[s['typeId']]['name'] == 'Link')
        option = next(s for s in result['schemas'] if arena[s['typeId']]['name'] == 'Option')
        self.assertEqual(link['fields'][0]['typeId'], option['typeId'])
        self.assertEqual(option['variants'][0]['fields'][0]['typeId'], link['typeId'])

    def test_module_identity_and_duplicate_roots(self):
        result = self.plan({
            'main.pxl': 'import "a.pxl" as a\nimport "b.pxl" as b\nfn main() { print(a.Item{value: 1}); print(a.Item{value: 2}); print(b.Item{value: "x"}) }',
            'a.pxl': 'export record Item { value: int }',
            'b.pxl': 'export record Item { value: string }',
        })
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(len(result['schemas']), 2)
        self.assertEqual(len({result['types'][s['typeId']]['module'] for s in result['schemas']}), 2)

    def test_function_container_and_capture_dependencies(self):
        result = self.plan('record Item { value: int }\nfn make() -> fn()->[Item] { let items = [Item{value: 2}]; return fn() = items }\nfn main() { print(make()()) }')
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual([result['types'][s['typeId']]['name'] for s in result['schemas']], ['Item'])

    def test_unreachable_types_are_omitted(self):
        result = self.plan('record Unused { value: int }\nfn unused() -> Unused = Unused{value: 1}\nfn main() {}')
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(result['schemas'], [])

    def test_expanding_generic_recursion_reports_a_bound(self):
        result = self.plan('record Nest[T] { children: [Nest[[T]]] }\nfn main() { print(Nest[int]{children: []}) }')
        self.assertTrue(result['diagnostics'])
        self.assertEqual(result['diagnostics'][0]['phase'], 'schemas')
        self.assertIn('depth 32', result['diagnostics'][0]['message'])

    def test_unused_catch_binding_requires_error_schema(self):
        result = self.plan('fn main() { try { assert(true) } catch err {} }')
        self.assertEqual(result['diagnostics'], [])
        error = next(s for s in result['schemas'] if result['types'][s['typeId']]['name'] == 'Error')
        self.assertEqual([f['name'] for f in error['fields']], ['kind', 'code', 'message', 'path', 'operation', 'expected', 'actual'])
        self.assertTrue(all(result['types'][f['typeId']]['name'] == 'string' for f in error['fields']))

    def test_unused_nested_function_does_not_contribute_schemas(self):
        result = self.plan('record Hidden { value: int }\nfn main() { fn unused() -> Hidden = Hidden{value: 1} }')
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(result['schemas'], [])

    def test_frontend_errors_are_preserved(self):
        result = self.plan('fn main() { print(missing) }')
        self.assertTrue(result['diagnostics'])
        self.assertEqual(result['schemas'], [])

    def test_materializes_compiler_runtime_types(self):
        result = self.plan(self.files, summary=True)
        self.assertEqual(result['diagnostics'], [])
        self.assertGreater(result['schemas'], 20)
