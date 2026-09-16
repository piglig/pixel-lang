import json
import unittest
from pathlib import Path
from unittest.mock import patch

from pixellang.project import compile_project
from pixellang.vm import VM
from tests.selfhost_runtime import SELFHOST_HEAP_LIMITS

ROOT = Path(__file__).resolve().parents[1]


class SelfHostedTypeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = {p.name: p.read_text() for p in (ROOT / 'selfhost').glob('*.pxl')}
        cls.bytecode = compile_project(cls.files)[1].bytecode

    def analyze(self, files):
        if isinstance(files, str):
            files = {'main.pxl': files}
        vm = VM(self.bytecode, **SELFHOST_HEAP_LIMITS, input_text=json.dumps({
            'source': 'main.pxl', 'text': '', 'operation': 'types', 'files': files,
        }), max_steps=160_000_000, max_depth=1024)
        with patch('pixellang.project.build_project', side_effect=AssertionError('delegated')), patch(
            'pixellang.text.parse_text', side_effect=AssertionError('delegated')
        ), patch('pixellang.semantics.Checker.check', side_effect=AssertionError('delegated')):
            return vm.run()[0]

    def test_resolves_own_source_annotations(self):
        result = self.analyze(self.files)
        self.assertEqual(result['diagnostics'], [])
        self.assertGreater(len(result['annotations']), 300)
        for annotation in result['annotations']:
            self.assertGreaterEqual(annotation['typeId'], 0)
            self.assertLess(annotation['typeId'], len(result['types']))

    def test_structural_types_are_interned(self):
        result = self.analyze('''record Data { left: map[[int]], right: map[[int]], callback: fn(int) }
fn main() {}
''')
        self.assertEqual(result['diagnostics'], [])
        definition = result['types'][result['definitions'][0]['typeId']]
        left, right, callback = definition['fields']
        self.assertEqual(left['typeId'], right['typeId'])
        typ = result['types'][left['typeId']]
        self.assertEqual(typ['kind'], 'map')
        array = result['types'][typ['arguments'][0]]
        self.assertEqual(array['kind'], 'array')
        self.assertEqual(result['types'][array['arguments'][0]]['name'], 'int')
        function = result['types'][callback['typeId']]
        self.assertEqual(function['kind'], 'function')
        self.assertEqual([result['types'][i]['name'] for i in function['arguments']], ['int', 'unit'])

    def test_generic_parameters_have_declaration_identity(self):
        result = self.analyze('''record First[T] { value: T }
record Second[T] { value: T }
fn main() {}
''')
        self.assertEqual(result['diagnostics'], [])
        defs = [result['types'][d['typeId']] for d in result['definitions']]
        self.assertNotEqual(defs[0]['arguments'], defs[1]['arguments'])
        for definition in defs:
            self.assertEqual(definition['fields'][0]['typeId'], definition['arguments'][0])

    def test_nominal_identity_and_visibility(self):
        result = self.analyze({
            'main.pxl': 'import "a.pxl" as a\nimport "a.pxl" as alias\nimport "b.pxl" as b\nrecord Use { x: a.Item[int], y: alias.Item[int], z: b.Item[int] }\nfn main() {}',
            'a.pxl': 'export record Item[T] { value: T }',
            'b.pxl': 'export record Item[T] { value: T }',
        })
        self.assertEqual(result['diagnostics'], [])
        fields = next(t['fields'] for t in result['types'] if t['name'] == 'Use')
        self.assertEqual(fields[0]['typeId'], fields[1]['typeId'])
        self.assertNotEqual(fields[0]['typeId'], fields[2]['typeId'])
        private = self.analyze({'main.pxl': 'import "a.pxl" as a\nrecord Use { x: a.Hidden }\nfn main() {}', 'a.pxl': 'record Hidden { value: int }'})
        self.assertIn('Type is private', private['diagnostics'][0]['message'])

    def test_enum_payload_and_recursive_record(self):
        result = self.analyze('''enum Choice[T] { Value(item: T), Empty }
record Link { next: Option[Link] }
fn main() {}
''')
        self.assertEqual(result['diagnostics'], [])
        choice = next(t for t in result['types'] if t['name'] == 'Choice')
        self.assertEqual([v['name'] for v in choice['variants']], ['Value', 'Empty'])
        self.assertEqual(choice['variants'][0]['fields'][0]['typeId'], choice['arguments'][0])
        link_id = next(i for i, t in enumerate(result['types']) if t['name'] == 'Link')
        option = result['types'][result['types'][link_id]['fields'][0]['typeId']]
        self.assertEqual(option['arguments'], [link_id])

    def test_invalid_annotations_report_type_errors(self):
        for declaration, expected in [
            ('record R { x: Missing }', 'Unknown type'),
            ('record R { x: map[int, string] }', 'Wrong type argument count'),
            ('record R { x: int[string] }', 'Primitive type cannot take arguments'),
            ('record Box[T] { x: T }\nrecord R { x: Box }', 'Wrong type argument count'),
            ('record R { x: int, x: string }', 'Duplicate field'),
            ('enum E { Same, Same }', 'Duplicate variant'),
            ('record Box[T, T] { x: T }', 'Duplicate type parameter'),
            ('record R { x: main }', 'Name is not a type'),
            ('record R { x: other.Item }', 'Unknown type module'),
        ]:
            with self.subTest(declaration=declaration):
                errors = self.analyze(declaration + '\nfn main() {}')['diagnostics']
                self.assertTrue(errors)
                self.assertEqual(errors[0]['phase'], 'type')
                self.assertIn(expected, errors[0]['message'])

    def test_generic_field_and_variant_substitution(self):
        files = dict(self.files)
        files['main.pxl'] = '''import "types.pxl" as types
import "bindings.pxl" as bindings
import "project.pxl" as project
fn main() {
 let files = jsonDecode[map[string]](input())
 let resolved = types.Analyze(bindings.Bind(project.Build(files, "main.pxl")))
 print(resolved.diagnostics)
 var use = -1
 for definition in resolved.definitions {
  if resolved.types[definition.typeId].name == "Use" { use = definition.typeId }
 }
 let fields = types.FieldTypes(resolved, use)
 let box = fields[0].typeId
 let boxFields = types.FieldTypes(resolved, box)
 let repeated = types.FieldTypes(resolved, box)
 print(boxFields[0].typeId == repeated[0].typeId)
 for field in boxFields {
  let typ = resolved.types[field.typeId]
  print(typ.kind)
  for argument in typ.arguments { print(resolved.types[argument].name) }
 }
 let variants = types.VariantTypes(resolved, fields[1].typeId)
 print(resolved.types[variants[0].fields[0].typeId].name)
 let option = types.VariantTypes(resolved, fields[2].typeId)
 print(option[0].name); print(resolved.types[option[0].fields[0].typeId].name)
 print(option[1].name); print(len(option[1].fields))
 print(len(types.FieldTypes(resolved, fields[3].typeId)))
}
'''
        bytecode = compile_project(files)[1].bytecode
        source = '''record Box[T] { values: [T], callback: fn(T)->T }
enum Choice[T] { Value(item: T), Empty }
record Use { box: Box[int], choice: Choice[string], option: Option[bool], error: Error }
fn main() {}
'''
        vm = VM(bytecode, input_text=json.dumps({'main.pxl': source}), max_steps=10_000_000, max_depth=1024)
        with patch('pixellang.text.parse_text', side_effect=AssertionError('delegated')):
            output = vm.run()
        self.assertEqual(output, [[], True, 'array', 'int', 'function', 'int', 'int', 'string', 'Some', 'bool', 'None', 0, 7])

    def test_function_signatures_and_annotated_symbols(self):
        result = self.analyze('''fn identity[T](value: T) -> T = value
fn consume(value: [int]) {}
fn main() {
 let amount: int = 1
 let transform = fn(x) = x
}
''')
        self.assertEqual(result['diagnostics'], [])
        symbols = result['symbols']
        identity = next(s for s in symbols if s['name'] == 'identity')
        signature = next(s for s in result['signatures'] if s['node'] == identity['node'])
        self.assertEqual(signature['typeId'], identity['typeId'])
        function = result['types'][identity['typeId']]
        self.assertEqual(function['kind'], 'function')
        self.assertEqual(function['arguments'], signature['parameters'] * 2)
        amount = next(s for s in symbols if s['name'] == 'amount')
        self.assertEqual(result['types'][amount['typeId']]['name'], 'int')
        consume = next(s for s in symbols if s['name'] == 'consume')
        function = result['types'][consume['typeId']]
        self.assertEqual(result['types'][function['arguments'][-1]]['name'], 'unit')
        self.assertTrue(any(s['typeId'] == -1 for s in result['signatures']))

    def test_type_parameter_does_not_escape_its_declaration(self):
        result = self.analyze('fn identity[T](value: T) -> T = value\nfn main() { let value: T = 1 }')
        self.assertEqual(result['diagnostics'][0]['message'], 'Unknown type: T')
