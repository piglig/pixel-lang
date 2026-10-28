import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.native_compiler import compile_native_sources
from pixellang.vm import VM
from tests.selfhost_runtime import SELFHOST_HEAP_LIMITS

ROOT = Path(__file__).resolve().parents[1]
DRIVER = '''import "model.pxl" as model
import "project.pxl" as project
import "bindings.pxl" as bindings
import "types.pxl" as types
import "inference.pxl" as inference
effect fn main() {
 let source = input()
 var resolved = types.Analyze(bindings.Bind(project.Build(map[string, string]{"main.pxl": source}, "main.pxl")))
 print(resolved.diagnostics)
 var signature = model.FunctionSignature{module: 0, node: 0, parameters: [], typeId: -1}
 for candidate in resolved.signatures {
  if resolved.bound.project.modules[candidate.module].syntax.nodes[candidate.node].text == "Target" { signature = candidate }
 }
 var arguments: [int] = []
 var expected = -1
 for definition in resolved.definitions {
  if resolved.types[definition.typeId].name == "Actual" {
   for field in types.FieldTypes(definition.typeId, &resolved) {
    if field.name == "result" { expected = field.typeId } else { arguments.append(field.typeId) }
   }
  }
 }
 let result = inference.Call(signature, arguments, expected, &resolved)
 print(result)
 print(resolved.types)
}
'''


class SelfHostedInferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files = {p.name: p.read_text() for p in (ROOT / 'selfhost').glob('*.pxl')}
        files['main.pxl'] = DRIVER
        cls.bytecode = compile_native_sources(files)

    def infer(self, source):
        vm = VM(self.bytecode, **SELFHOST_HEAP_LIMITS, input_text=source + '\nfn main() {}', max_steps=20_000_000, max_depth=1024)
        with patch('pixellang.text.parse_text', side_effect=AssertionError('delegated')):
            diagnostics, result, arena = vm.run()
        self.assertEqual(diagnostics, [])
        return result, arena

    def test_nested_container_constraints(self):
        result, arena = self.infer('fn Target[T](value: map[string, [T]]) -> T {}\nrecord Actual { value: map[string, [int]] }')
        self.assertEqual(result['conflicts'], [])
        self.assertEqual([arena[i]['name'] for i in result['arguments']], ['int'])
        self.assertEqual(arena[arena[result['typeId']]['arguments'][-1]]['name'], 'int')

    def test_callback_and_expected_result_constraints(self):
        result, arena = self.infer('fn Target[T, U](value: [T], callback: fn(T)->U) -> [U] {}\nrecord Actual { value: [int], callback: fn(int)->string, result: [string] }')
        self.assertEqual(result['conflicts'], [])
        self.assertEqual([arena[i]['name'] for i in result['arguments']], ['int', 'string'])
        result, arena = self.infer('fn Target[T]() -> [T] {}\nrecord Actual { result: [bool] }')
        self.assertEqual(result['conflicts'], [])
        self.assertEqual(arena[result['arguments'][0]]['name'], 'bool')

    def test_repeated_parameter_conflict(self):
        result, _ = self.infer('fn Target[T](a: T, b: T) -> T {}\nrecord Actual { a: int, b: string }')
        self.assertEqual(result['typeId'], -1)
        self.assertEqual(result['conflicts'][0]['path'], 'argument.1')
        self.assertIn('Conflicting', result['conflicts'][0]['message'])

    def test_nominal_shapes_are_not_structural_records(self):
        result, _ = self.infer('record Box[T] { value: T }\nrecord Other[T] { value: T }\nfn Target[T](value: Box[T]) -> T {}\nrecord Actual { value: Other[int] }')
        self.assertEqual(result['typeId'], -1)
        self.assertIn('shapes', result['conflicts'][0]['message'])

    def test_missing_parameters_and_argument_count(self):
        result, _ = self.infer('fn Target[T]() {}\nrecord Actual {}')
        self.assertEqual(result['typeId'], -1)
        self.assertIn('Cannot infer', result['conflicts'][0]['message'])
        result, _ = self.infer('fn Target[T](value: T) {}\nrecord Actual {}')
        self.assertEqual(result['typeId'], -1)
        self.assertEqual(result['conflicts'][0]['path'], 'arguments')

    def test_expected_result_can_conflict_with_arguments(self):
        result, _ = self.infer('fn Target[T](value: T) -> T {}\nrecord Actual { value: int, result: string }')
        self.assertEqual(result['typeId'], -1)
        self.assertEqual(result['conflicts'][0]['path'], 'result')

    def test_generic_caller_parameters_are_valid_symbolic_arguments(self):
        result, arena = self.infer('fn Target[T](value: T) -> T {}\nrecord Actual[U] { value: U }')
        self.assertEqual(result['conflicts'], [])
        self.assertEqual(arena[result['arguments'][0]]['kind'], 'type_parameter')
        self.assertEqual(arena[result['arguments'][0]]['name'], 'U')

    def test_generic_collection_values_keep_the_same_type(self):
        result, arena = self.infer('fn Target[T](seed: T, value: T) -> T {}\nrecord Actual { seed: [int], value: [int], result: [int] }')
        self.assertEqual(result['conflicts'], [])
        self.assertEqual(arena[result['arguments'][0]]['kind'], 'array')
        result_type = arena[result['typeId']]['arguments'][-1]
        self.assertEqual(result_type, result['arguments'][0])

    def test_former_projection_name_is_an_ordinary_inferable_record(self):
        result, arena = self.infer('record Frozen[T] { value:T }\nfn Target[T](value: Frozen[T])->T {}\nrecord Actual { value: Frozen[[int]], result:[int] }')
        self.assertEqual(result['conflicts'], [])
        inferred = arena[result['arguments'][0]]
        self.assertEqual(inferred['kind'], 'array')
        self.assertEqual(arena[inferred['arguments'][0]]['name'], 'int')

    def test_generic_values_do_not_project_away_conflicting_types(self):
        result, _ = self.infer('fn Target[T](seed: T, value: T) {}\nrecord Actual { seed: [int], value: [string] }')
        self.assertEqual(result['typeId'], -1)
        self.assertTrue(any('Conflicting type parameter constraints' in c['message'] for c in result['conflicts']))

    def test_staged_constraints_preview_and_context_isolation(self):
        files = {p.name: p.read_text() for p in (ROOT / 'selfhost').glob('*.pxl')}
        prefix = DRIVER[:DRIVER.index('effect fn main()')]
        files['main.pxl'] = prefix + '''effect fn main() {
 var resolved = types.Analyze(bindings.Bind(project.Build(map[string, string]{"main.pxl": input()}, "main.pxl")))
 print(resolved.diagnostics)
 var signature = model.FunctionSignature{module: 0, node: 0, parameters: [], typeId: -1}
 var actual = -1
 for candidate in resolved.signatures {
  if resolved.bound.project.modules[candidate.module].syntax.nodes[candidate.node].text == "Target" { signature = candidate }
 }
 for definition in resolved.definitions { if resolved.types[definition.typeId].name == "Actual" { actual = definition.typeId } }
 let fields = types.FieldTypes(actual, &resolved)
 var context = inference.Begin(signature.parameters)
 let independent = context
 let function = resolved.types[signature.typeId]
 inference.Constrain(function.arguments[0], fields[0].typeId, "first", resolved, &context)
 let previewId = inference.Preview(context, function.arguments[1], &resolved)
 let preview = resolved.types[previewId]
 print(resolved.types[preview.arguments[0]].name)
 print(resolved.types[preview.arguments[1]].name)
 print(inference.Complete(context, signature.typeId, &resolved).typeId == -1)
 print(len(context.conflicts))
 inference.Constrain(function.arguments[1], fields[1].typeId, "callback", resolved, &context)
 let completed = inference.Complete(context, signature.typeId, &resolved)
 print(len(completed.conflicts)); print(completed.typeId >= 0)
 print(independent.arguments)
 context.arguments[0] = -1
 context.conflicts.append(inference.Conflict{path: "later", message: "later", formal: -1, actual: -1})
 print(completed.arguments[0] >= 0)
 print(len(completed.conflicts))
}
'''
        bytecode = compile_native_sources(files)
        source = 'fn Target[T, U](value: T, callback: fn(T)->U) -> U {}\nrecord Actual { value: int, callback: fn(int)->string }\nfn main() {}'
        vm = VM(bytecode, input_text=source, max_steps=20_000_000, max_depth=1024)
        with patch('pixellang.text.parse_text', side_effect=AssertionError('delegated')):
            self.assertEqual(vm.run(), [[], 'int', 'U', True, 0, 0, True, [-1, -1], True, 0])
