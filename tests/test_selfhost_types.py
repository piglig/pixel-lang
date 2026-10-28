import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.native_compiler import compile_native_sources, execute_native_driver
from pixellang.vm import VM
from tests.selfhost_runtime import SELFHOST_HEAP_LIMITS, SELFHOST_MAX_STEPS, SELFHOST_MAX_OUTPUT_CHARS

ROOT = Path(__file__).resolve().parents[1]


class SelfHostedTypeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = {p.name: p.read_text() for p in (ROOT / 'selfhost').glob('*.pxl')}
        cls.bytecode = compile_native_sources(cls.files)

    def analyze(self, files, *, max_steps=160_000_000, max_output_chars=1_000_000):
        if isinstance(files, str):
            files = {'main.pxl': files}
        vm = VM(self.bytecode, **SELFHOST_HEAP_LIMITS, input_text=json.dumps({
            'source': 'main.pxl', 'text': '', 'operation': 'types', 'files': files,
        }), max_steps=max_steps, max_depth=1024, max_output_chars=max_output_chars)
        with patch(
            'pixellang.text.parse_text', side_effect=AssertionError('delegated')
        ):
            return vm.run()[0]

    def test_resolves_own_source_annotations(self):
        result = execute_native_driver(self.bytecode,
            dict(source='main.pxl', text='', operation='types', files=self.files),
            steps=SELFHOST_MAX_STEPS, output_chars=SELFHOST_MAX_OUTPUT_CHARS)[0]
        self.assertEqual(result['diagnostics'], [])
        self.assertGreater(len(result['annotations']), 300)
        for annotation in result['annotations']:
            self.assertGreaterEqual(annotation['typeId'], 0)
            self.assertLess(annotation['typeId'], len(result['types']))

    def test_structural_types_are_interned(self):
        result = self.analyze('''record Data { left: map[string, [int]], right: map[string, [int]], callback: fn(int) }
fn main() {}
''')
        self.assertEqual(result['diagnostics'], [])
        definition = result['types'][result['definitions'][0]['typeId']]
        left, right, callback = definition['fields']
        self.assertEqual(left['typeId'], right['typeId'])
        typ = result['types'][left['typeId']]
        self.assertEqual(typ['kind'], 'map')
        self.assertEqual(result['types'][typ['arguments'][0]]['name'], 'string')
        array = result['types'][typ['arguments'][1]]
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
record Chain { next: Option[Chain] }
fn main() {}
''')
        self.assertEqual(result['diagnostics'], [])
        choice = next(t for t in result['types'] if t['name'] == 'Choice')
        self.assertEqual([v['name'] for v in choice['variants']], ['Value', 'Empty'])
        self.assertEqual(choice['variants'][0]['fields'][0]['typeId'], choice['arguments'][0])
        link_id = next(i for i, t in enumerate(result['types']) if t['name'] == 'Chain')
        option = result['types'][result['types'][link_id]['fields'][0]['typeId']]
        self.assertEqual(option['arguments'], [link_id])

    def test_invalid_annotations_report_type_errors(self):
        for declaration, expected in [
            ('record R { x: Missing }', 'Unknown type'),
            ('record R { x: map[int, string, int] }', 'Wrong type argument count'),
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
effect fn main() {
 match jsonDecode::[map[string, string]](input()) {
  .Ok(files) => { Dispatch(files) },
  .Err(error) => { print(error.message) }
 }
}
effect fn Dispatch(files:map[string,string]) {
 var resolved = types.Analyze(bindings.Bind(project.Build(files, "main.pxl")))
 print(resolved.diagnostics)
 var use = -1
 for definition in resolved.definitions {
  if resolved.types[definition.typeId].name == "Use" { use = definition.typeId }
 }
 let fields = types.FieldTypes(use, &resolved)
 let box = fields[0].typeId
 let boxFields = types.FieldTypes(box, &resolved)
 let repeated = types.FieldTypes(box, &resolved)
 print(boxFields[0].typeId == repeated[0].typeId)
 for field in boxFields {
  let typ = resolved.types[field.typeId]
  print(typ.kind)
  for argument in typ.arguments { print(resolved.types[argument].name) }
 }
 let variants = types.VariantTypes(fields[1].typeId, &resolved)
 print(resolved.types[variants[0].fields[0].typeId].name)
 let option = types.VariantTypes(fields[2].typeId, &resolved)
 print(option[0].name); print(resolved.types[option[0].fields[0].typeId].name)
 print(option[1].name); print(len(option[1].fields))
 print(len(types.FieldTypes(fields[3].typeId, &resolved)))
}
'''
        bytecode = compile_native_sources(files)
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

    def test_native_type_analysis_matches_vm(self):
        for source in ('record Box[T]{value:T}\nfn main(){let box=Box{value:1}}',
                       'record Box{value:Missing}\nfn main(){}'):
            with self.subTest(source=source):
                result = execute_native_driver(self.bytecode,
                    dict(source='main.pxl', text='', operation='types',
                         files={'main.pxl': source}))[0]
                self.assertEqual(result, SelfHostedTypeTests.analyze(self, source))

    def test_removed_type_constructors_are_unknown_without_declarations(self):
        for annotation in ('Frozen[int]', 'Frozen[[int]]', '[Frozen[int]]',
                           'map[string,Frozen[int]]', 'mut [int]'):
            with self.subTest(annotation=annotation):
                result = self.analyze('record R { value: ' + annotation + ' }\nfn main() {}')
                self.assertTrue(result['diagnostics'])
                self.assertTrue(any('Unknown type' in item['message'] for item in result['diagnostics']))
                self.assertFalse(any(t['kind'] in ('mutable', 'frozen') for t in result['types']))

    def test_former_spellings_can_name_ordinary_nominal_types(self):
        result = self.analyze('''record Frozen[T] { value:T }
record mut[T] { value:T }
record Use { first:Frozen[int], second:mut[string] }
fn main() {}
''')
        self.assertEqual(result['diagnostics'], [])
        for name in ('Frozen', 'mut'):
            found = [t for t in result['types'] if t['name'] == name]
            self.assertTrue(found)
            self.assertTrue(all(t['kind'] == 'record' and t['module'] >= 0 for t in found))

    def test_inout_is_an_access_mode_over_an_ordinary_generic_value(self):
        result = self.analyze('''record Box[T] { value:T }
fn replace[T](target:inout Box[T], value:T) { target.value=value }
fn main() {}
''')
        self.assertEqual(result['diagnostics'], [])
        mode, = [t for t in result['types'] if t['kind'] == 'inout']
        box = result['types'][mode['arguments'][0]]
        self.assertEqual(box['kind'], 'record')
        self.assertEqual(box['name'], 'Box')
        self.assertEqual(result['types'][box['arguments'][0]]['kind'], 'type_parameter')
        self.assertFalse(any(t['kind'] in ('mutable', 'frozen') for t in result['types']))
