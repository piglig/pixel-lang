import json
import unittest
from pathlib import Path
from unittest.mock import patch

from pixellang.project import compile_project
from pixellang.vm import VM
from tests.selfhost_runtime import SELFHOST_HEAP_LIMITS

ROOT = Path(__file__).resolve().parents[1]


class SelfHostedCheckerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = {p.name: p.read_text() for p in (ROOT / 'selfhost').glob('*.pxl')}
        cls.bytecode = compile_project(cls.files)[1].bytecode

    def check(self, source):
        files = source if isinstance(source, dict) else {'main.pxl': source}
        vm = VM(self.bytecode, **SELFHOST_HEAP_LIMITS, input_text=json.dumps({
            'source': 'main.pxl', 'text': '', 'operation': 'check', 'files': files,
        }), max_steps=30_000_000, max_depth=1024)
        with patch('pixellang.text.parse_text', side_effect=AssertionError('delegated')), patch(
            'pixellang.semantics.Checker.check', side_effect=AssertionError('delegated')
        ):
            return vm.run()[0]

    def test_arithmetic_arrays_and_mutable_variables(self):
        result = self.check('''fn main() {
 var count = 1
 count += 2
 let values = [count, 4]
 values[0] = 3
 append(values, 5)
 assert(len(values) == 3)
 print(values[0] + 2 * 4)
}
''')
        self.assertEqual(result['diagnostics'], [])
        self.assertTrue(result['expressions'])
        self.assertTrue(all(n['typeId'] >= 0 for n in result['expressions']))

    def test_unit_calls_are_statements_not_storable_values(self):
        prefix = 'fn effect() {}\n'
        self.assertEqual(self.check(prefix + 'fn main() { effect() }')['diagnostics'], [])
        self.assertEqual(self.check('fn callback() -> fn() { return fn() {} }\nfn main() { let run = callback(); run() }')['diagnostics'], [])
        for body in ('let value = effect()', 'var value = effect()', 'print(effect())',
                     'let values = [effect()]', 'jsonFrom(effect())'):
            with self.subTest(body=body):
                errors = self.check(prefix + 'fn main() { ' + body + ' }')['diagnostics']
                self.assertTrue(errors)
                self.assertTrue(any('require' in item['message'] and 'value' in item['message'] for item in errors))

    def test_generic_calls_and_explicit_function_values(self):
        result = self.check('''fn identity[T](value: T) -> T = value
fn main() {
 let integer: int = identity(1)
 let text: string = identity("ok")
 let specialized = identity[int]
 print(specialized(integer))
}
''')
        self.assertEqual(result['diagnostics'], [])

    def test_contextual_callback_with_inferred_result(self):
        result = self.check('''fn apply[T, U](callback: fn(T)->U, value: T) -> U = callback(value)
fn main() { let result: bool = apply(fn(x) = x > 0, 1); assert(result) }
''')
        self.assertEqual(result['diagnostics'], [])

    def test_expected_result_infers_empty_array(self):
        result = self.check('''fn empty[T]() -> [T] = []
fn main() { let values: [int] = empty(); print(len(values)) }
''')
        self.assertEqual(result['diagnostics'], [])

    def test_mismatch_and_call_errors(self):
        for source in [
            'fn main() { let value: int = "bad" }',
            'fn main() { print(1 + true) }',
            'fn main() { let a = [1, "bad"] }',
            'fn main() { let a = [1]; print(a["bad"]) }',
            'fn value(a: int) -> int = a\nfn main() { print(value()) }',
            'fn same[T](a: T, b: T) -> T = a\nfn main() { same(1, "bad") }',
        ]:
            with self.subTest(source=source):
                errors = self.check(source)['diagnostics']
                self.assertTrue(errors)
                self.assertEqual(errors[0]['phase'], 'check')

    def test_return_paths_and_loop_boundaries(self):
        valid = self.check('fn choose(value: bool) -> int { if value { return 1 } else { return 2 } }\nfn main() { while false { break } }')
        self.assertEqual(valid['diagnostics'], [])
        for source, message in [
            ('fn missing(value: bool) -> int { if value { return 1 } }\nfn main() {}', 'Not every path'),
            ('fn main() { break }', 'Loop control outside'),
            ('fn main() { while true { let action = fn() { break }; break } }', 'Loop control outside'),
            ('fn wrong() -> int { return "bad" }\nfn main() {}', 'Type mismatch'),
        ]:
            with self.subTest(source=source):
                self.assertTrue(any(message in e['message'] for e in self.check(source)['diagnostics']))

    def test_imported_generic_function_call(self):
        result = self.check({
            'main.pxl': 'import "lib.pxl" as lib\nfn main() { let result: int = lib.Identity(3) }',
            'lib.pxl': 'export fn Identity[T](value: T) -> T = value',
        })
        self.assertEqual(result['diagnostics'], [])

    def test_records_maps_and_generic_record_inference(self):
        result = self.check('record R[T] { value: T }\nfn main() { let r = R{value: 1}; let explicit = R[string]{value: "ok"}; let m = map[int]{"key": r.value}; m["key"] = 2; print(explicit.value) }')
        self.assertEqual(result['diagnostics'], [])

    def test_invalid_record_initializers(self):
        for expression in ('R{}', 'R{other: 1}', 'R{value: "bad"}', 'R{value: 1, value: 2}'):
            self.assertTrue(self.check('record R { value: int }\nfn main() { let r = ' + expression + ' }')['diagnostics'])

    def test_invalid_assignment_target(self):
        self.assertTrue(self.check('fn main() { 1 = 2 }')['diagnostics'])

    def test_loops_option_matches_and_catch_bindings(self):
        result = self.check('''fn choose(value: Option[int]) -> int {
 return match value { Some(item) => item, None => 0 }
}
fn main() {
 var total = 0
 for index, value in [1, 2] { total += index + value }
 let values = map[int]{"x": total}
 for key, value in values { print(key); total += value }
 let optional: Option[int] = None
 print(choose(Some(total)))
 try { fail("test") } catch error { print(error.message) }
}
''')
        self.assertEqual(result['diagnostics'], [])

    def test_match_exhaustiveness_and_payload_checks(self):
        for arms, message in [
            ('Some(x) => x', 'Missing match arm: None'),
            ('Some(x, y) => x, None => 0', 'Pattern payload arity mismatch'),
            ('Some(x) => x, Some(y) => y, None => 0', 'Duplicate or unreachable'),
            ('Some(x) => x, None => "bad"', 'Type mismatch'),
        ]:
            source = 'fn select(value: Option[int]) -> int { return match value { ' + arms + ' } }\nfn main() {}'
            self.assertTrue(any(message in e['message'] for e in self.check(source)['diagnostics']))

    def test_builtin_result_types_and_json_decode(self):
        result = self.check('''record R { value: int }
fn main() {
 let a = [1]
 let appended: [int] = append(a, 2)
 let checked: bool = assert(true)
 let pieces: [string] = split("a,b", ",")
 let joined: string = join(pieces, ",")
 let values = map[int]{"x": 1}
 let removed: bool = delete(values, "x")
 let found: Option[int] = lookup(values, "x")
 let recordValue = jsonDecode[R]("{\\"value\\":1}")
 print(recordValue.value)
}
''')
        self.assertEqual(result['diagnostics'], [])

    def test_generic_enum_construction_and_matching(self):
        result = self.check('''enum Choice[T] { Value(item: T), Empty }
fn choose(value: Choice[int]) -> int {
 return match value { Choice.Value(item) => item, Choice.Empty => 0 }
}
fn main() {
 let inferred = Choice.Value(3)
 let explicit = Choice[string].Value("ok")
 let empty = Choice[int].Empty
 print(choose(inferred))
}
''')
        self.assertEqual(result['diagnostics'], [])

    def test_own_compiler_expression_check(self):
        request = {'source': 'main.pxl', 'text': '', 'operation': 'check-summary', 'files': self.files}
        vm = VM(self.bytecode, **SELFHOST_HEAP_LIMITS, input_text=json.dumps(request), max_steps=300_000_000, max_depth=1024)
        with patch('pixellang.text.parse_text', side_effect=AssertionError('delegated')), patch(
            'pixellang.semantics.Checker.check', side_effect=AssertionError('delegated')
        ):
            result = vm.run()[0]
        self.assertEqual(result['diagnostics'], [])
        self.assertGreater(result['expressions'], 5000)
        self.assertEqual(result['unresolved'], 0)


    def test_generic_call_resolutions_are_kept_for_lowering(self):
        result = self.check('fn identity[T](value: T) -> T = value\nfn main() { let a = identity(1); let f = identity[string]; let b = f("ok") }')
        self.assertEqual(result['diagnostics'], [])
        generic = [call for call in result['calls'] if call['arguments']]
        self.assertEqual([call['kind'] for call in generic], ['call', 'value'])
        self.assertEqual([result['types'][call['arguments'][0]]['name'] for call in generic], ['int', 'string'])
        for call in result['calls']:
            self.assertEqual(result['types'][call['functionType']]['kind'], 'function')

    def test_signed_integer_literal_boundaries(self):
        for literal in ('9223372036854775807', '-9223372036854775808', '0', '00000000000000000001'):
            with self.subTest(literal=literal):
                self.assertEqual(self.check('fn main() { print(' + literal + ') }')['diagnostics'], [])
        for literal in ('9223372036854775808', '-9223372036854775809', '99999999999999999999'):
            with self.subTest(literal=literal):
                errors = self.check('fn main() { print(' + literal + ') }')['diagnostics']
                self.assertTrue(any('signed 64-bit' in error['message'] for error in errors))

    def test_non_scalar_equality_and_invalid_ordering_are_rejected(self):
        for expression in ('[1] == [1]', 'map[int]{} != map[int]{}', 'true < false', 'jsonNull() == jsonNull()'):
            with self.subTest(expression=expression):
                self.assertTrue(self.check('fn main() { print(' + expression + ') }')['diagnostics'])
        self.assertEqual(self.check('fn main() { assert(true == false); assert("a" < "b"); assert(1.0 != 2.0) }')['diagnostics'], [])

    def test_compound_assignments_check_the_underlying_operator(self):
        for code in ('var b = true; b += false', 'var a = [1]; a += [2]', 'var s = "x"; s *= "y"', 'let s = "x"; s[0] = "y"'):
            with self.subTest(code=code):
                self.assertTrue(self.check('fn main() { ' + code + ' }')['diagnostics'])
        self.assertEqual(self.check('fn main() { var s = "x"; s += "y"; var n = 2.0; n %= 1.5 }')['diagnostics'], [])

    def test_explicit_generic_match_patterns(self):
        result = self.check('''enum Choice[T] { Value(item: T), Empty }
fn extract[T](value: Choice[T], fallback: T) -> T {
 return match value { Choice[T].Value(item) => item, Choice[T].Empty => fallback }
}
fn main() { print(extract(Choice[int].Value(1), 0)) }
''')
        self.assertEqual(result['diagnostics'], [])
        invalid = self.check('enum Choice[T] { Value(item: T) }\nfn extract(value: Choice[int]) -> int { return match value { Choice[string].Value(item) => item } }\nfn main() {}')
        self.assertTrue(any('Pattern type does not match subject' in e['message'] for e in invalid['diagnostics']))

    def test_imported_explicit_generic_match_pattern(self):
        result = self.check({
            'lib.pxl': 'export enum Choice[T] { Value(item: T) }',
            'main.pxl': 'import "lib.pxl" as lib\nfn extract(value: lib.Choice[int]) -> int { return match value { lib.Choice[int].Value(item) => item } }\nfn main() { print(extract(lib.Choice[int].Value(1))) }',
        })
        self.assertEqual(result['diagnostics'], [])

    def test_pattern_type_parameters_are_scoped(self):
        result = self.check('enum Choice[T] { Value(item: T) }\nfn extract(value: Choice[int]) -> int { return match value { Choice[T].Value(item) => item } }\nfn main() {}')
        self.assertTrue(any('Unknown type: T' in e['message'] for e in result['diagnostics']))

    def test_imported_generic_function_values_require_specialization(self):
        library = 'export fn Identity[T](value: T) -> T = value'
        invalid = self.check({'lib.pxl': library, 'main.pxl': 'import "lib.pxl" as lib\nfn main() { let f = lib.Identity }'})
        self.assertTrue(any('requires explicit specialization' in e['message'] for e in invalid['diagnostics']))
        valid = self.check({'lib.pxl': library, 'main.pxl': 'import "lib.pxl" as lib\nfn main() { let f = lib.Identity[int]; print(f(1)) }'})
        self.assertEqual(valid['diagnostics'], [])

    def test_unconstrained_generic_scalar_operations_are_rejected(self):
        for expression in ('a == b', 'a < b', 'a + b'):
            with self.subTest(expression=expression):
                result = self.check('fn invalid[T](a: T, b: T) { print(' + expression + ') }\nfn main() {}')
                self.assertTrue(result['diagnostics'])

    def test_real_project_semantic_checks(self):
        library = {'std/' + p.name: p.read_text() for p in (ROOT / 'pixellang' / 'stdlib').glob('*.pxl')}
        for name in ('ledger', 'log-analysis', 'callback-analysis'):
            with self.subTest(project=name):
                files = {p.name: p.read_text() for p in (ROOT / 'examples' / name).glob('*.pxl')}
                files.update(library)
                result = self.check(files)
                self.assertEqual(result['diagnostics'], [])
                self.assertTrue(all(e['typeId'] >= 0 for e in result['expressions']))

    def test_type_names_are_not_runtime_values(self):
        for source in (
            'record Box { value: int }\nfn main() { let value = Box }',
            'record Box[T] { value: T }\nfn main() { print(Box[int]) }',
            'enum Choice { A, B }\nfn main() { let value = Choice }',
            'fn main() { let value = Error }',
            'record Box { value: int }\nfn main() { print(Box.value) }',
            'enum Choice { A, B }\nfn main() { let value = Choice.A; print(value.B) }',
            'enum Choice { Value(item: int) }\nfn main() { let create = Choice.Value }',
            'enum Choice[T] { Value(item: T) }\nfn main() { let create = Choice[int].Value }',
            'record Box { value: int }\nfn main() { let value = Box{value: 1}; let other = value{value: 2} }',
        ):
            with self.subTest(source=source):
                self.assertTrue(self.check(source)['diagnostics'])
        for expression in ('lib.Box', 'lib.Box[int]'):
            result = self.check({'main.pxl': 'import "lib.pxl" as lib\nfn main() { let value = ' + expression + ' }',
                                 'lib.pxl': 'export record Box[T] { value: T }'})
            self.assertTrue(result['diagnostics'])

    def test_type_contexts_and_function_values_remain_valid(self):
        result = self.check({
            'main.pxl': '''import "lib.pxl" as lib
fn main() {
 let box = lib.Box[int]{value: 42}
 let value = lib.Choice[int].Value(box.value)
 let same = lib.identity[int]
 print(same(box.value))
}''',
            'lib.pxl': '''export record Box[T] { value: T }
export enum Choice[T] { Value(item: T), Empty }
export fn identity[T](value: T) -> T = value''',
        })
        self.assertEqual(result['diagnostics'], [])

    def test_underscore_variant_does_not_make_match_exhaustive(self):
        for source in (
            'enum C { _, Other }\nfn main() { let c = C.Other; print(match c { C._ => 1 }) }',
            'enum C[T] { _(value: T), Other }\nfn main() { let c = C[int].Other; print(match c { C[int]._(n) => n }) }',
            'enum C { A }\nfn main() { let c = C.A; print(match c { _(n) => 1 }) }',
        ):
            with self.subTest(source=source):
                self.assertTrue(self.check(source)['diagnostics'])
