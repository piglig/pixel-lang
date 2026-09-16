import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from pixellang.model import PixelError
from pixellang.fileaccess import FileAccess
from pixellang.project import compile_project
from pixellang.vm import VM
from tests.selfhost_runtime import SELFHOST_HEAP_LIMITS

ROOT = Path(__file__).resolve().parents[1]


class SelfHostedIRTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = {p.name: p.read_text() for p in (ROOT / 'selfhost').glob('*.pxl')}
        cls.bytecode = compile_project(cls.files)[1].bytecode

    def lower(self, files, operation="ir"):
        if isinstance(files, str):
            files = {'main.pxl': files}
        vm = VM(self.bytecode, **SELFHOST_HEAP_LIMITS, input_text=json.dumps({
            'source': 'main.pxl', 'text': '', 'operation': operation, 'files': files,
        }), max_steps=100_000_000, max_depth=1024)
        with patch('pixellang.text.parse_text', side_effect=AssertionError('delegated')), patch(
            'pixellang.semantics.Checker.check', side_effect=AssertionError('delegated')
        ), patch('pixellang.backend.lower', side_effect=AssertionError('delegated')), patch(
            'pixellang.backend.assemble', side_effect=AssertionError('delegated')
        ):
            return json.loads(vm.run()[0])

    def run_source(self, files, expected):
        result = self.lower(files, "bytecode")
        self.assertEqual(result['diagnostics'], [])
        bytecode = result['bytecode']
        with patch('pixellang.text.parse_text', side_effect=AssertionError('delegated')):
            self.assertEqual(VM(bytecode, max_steps=1_000_000).run(), expected)
        return bytecode

    def test_arithmetic_and_integer_boundary(self):
        self.run_source('fn main() { print(2 + 3 * 4); print(-9223372036854775808); print("a" + "b"); print(1.5 + 2.25) }', [14, -9223372036854775808, 'ab', 3.75])

    def test_loop_break_continue_and_shadowing(self):
        self.run_source('fn main() { var i = 0; var total = 0; while i < 10 { i++; if i == 2 { continue }; if i == 5 { break }; total += i }; if total == 8 { let total = "ok"; print(total) } else { print("bad") }; print(total) }', ['ok', 8])

    def test_short_circuit_skips_effects(self):
        self.run_source('fn effect() -> bool { print("effect"); return true }\nfn main() { print(false && effect()); print(true || effect()); print(true && effect()) }', [False, True, 'effect', True])

    def test_generic_calls_recursion_and_modules(self):
        self.run_source({
            'main.pxl': 'import "lib.pxl" as lib\nfn main() { print(lib.identity(lib.fact(6))); print(lib.identity("x")) }',
            'lib.pxl': 'export fn identity[T](value: T) -> T { return value }\nexport fn fact(n: int) -> int { if n <= 1 { return 1 }; return n * fact(n - 1) }',
        }, [720, 'x'])

    def test_arrays_records_and_shorthand(self):
        self.run_source('record Box[T] { value: T }\nfn main() { let value = [3, 5]; let box = Box[[int]]{value}; print(box.value[1]); print(len(value)) }', [5, 2])

    def test_escaping_mutable_and_sibling_closures(self):
        self.run_source('fn main() { var count = 0; let increment = fn() { count += 1 }; let read = fn() = count; increment(); increment(); print(read()) }', [2])
        self.run_source('fn make[T](value: T) -> fn()->T { return fn() = value }\nfn main() { let a = make(3); let b = make("x"); print(a()); print(b()) }', [3, 'x'])

    def test_nested_closure_and_fresh_while_binding(self):
        self.run_source('fn main() { var i = 0; let callbacks: [fn()->int] = []; while i < 3 { let value = i; append(callbacks, fn() = value); i++ }; print(callbacks[0]()); print(callbacks[2]()); var x = 7; let outer = fn() -> fn()->int { return fn() = x }; print(outer()()) }', [0, 2, 7])

    def test_callable_is_evaluated_before_arguments(self):
        self.run_source('fn main() { var f = fn(x: int) = x + 1; let change = fn() -> int { f = fn(x: int) = x + 100; return 5 }; print(f(change())); print(f(5)) }', [6, 105])

    def test_mutation_and_sorting(self):
        self.run_source('record Box { value: int }\nfn main() { let box = Box{value: 1}; box.value += 2; print(box.value); let values = [4, 1, 3, 2]; var i = 0; while i < len(values) { var j = i + 1; while j < len(values) { if values[j] < values[i] { let temp = values[i]; values[i] = values[j]; values[j] = temp }; j++ }; i++ }; values[0] += 1; print(values) }', [3, [2, 2, 3, 4]])

    def test_maps_and_runtime_error_provenance(self):
        self.run_source('fn main() { let counts = map[int]{"a": 1}; counts["a"] += 2; counts["b"] = 4; print(counts["a"]); print(len(counts)) }', [3, 2])
        source = 'fn main() {\n    print(1 / 0)\n}'
        result = self.lower(source, "bytecode")
        self.assertEqual(result['diagnostics'], [])
        with self.assertRaises(PixelError) as caught:
            VM(result['bytecode']).run()
        span = caught.exception.to_dict()['span']
        self.assertEqual(span['source'], 'main.pxl')
        self.assertEqual(span['start'], {'line': 2, 'column': 11, 'offset': source.index('1 / 0')})

    def test_for_array_string_and_map_snapshots(self):
        self.run_source('fn main() { let values = [1, 2]; for index, value in values { print(index); print(value); values[1] = 9; append(values, 3) }; for index, char in "a界" { print(index); print(char) }; let data = map[int]{"x": 1, "y": 2}; for key, value in data { print(key); print(value); data["y"] = 9; data["z"] = 3 }; for value in ([]: [int]) { print(value) } }', [0, 1, 1, 2, 0, 'a', 1, '界', 'x', 1, 'y', 2])

    def test_for_source_runs_once_and_single_map_yields_keys(self):
        self.run_source('fn values() -> [int] { print("source"); return [1, 2, 3] }\nfn main() { for value in values() { if value == 2 { continue }; print(value) }; for key in map[int]{"a": 7, "b": 8} { print(key) } }', ['source', 1, 3, 'a', 'b'])

    def test_for_closures_have_fresh_pair_bindings(self):
        self.run_source('fn main() { let callbacks: [fn()->int] = []; for index, value in [10, 20, 30] { append(callbacks, fn() = index + value) }; print(callbacks[0]()); print(callbacks[2]()) }', [10, 32])

    def test_nested_for_temporaries_and_shallow_snapshot(self):
        ir = self.run_source('record Item { value: int }\nfn main() { let item = Item{value: 1}; let values = [item, item]; var sum = 0; for value in values { value.value += 1; print(value.value); for number in [1, 2] { sum += number } }; print(sum); print(item.value) }', [2, 3, 6, 3])
        main = ir['functions'][ir['entry'][0]]
        self.assertGreaterEqual(main['locals'], 9)

    def test_loop_transfers_unwind_only_exited_handlers(self):
        self.run_source('fn main() { try { for value in [1, 2] { try { if value == 1 { continue }; break } catch inner { print("wrong") } }; print(1 / 0) } catch outer { print(outer.code) }; for value in [1] { try { break } catch inner { print("wrong") } }; try { print(1 / 0) } catch final { print(final.code) } }', ['numeric.division_by_zero', 'numeric.division_by_zero'])
        self.run_source('fn main() { var i = 0; try { while i < 2 { i++; try { continue } catch inner { print("wrong") } }; print(1 / 0) } catch outer { print(outer.code) } }', ['numeric.division_by_zero'])

    def test_nested_catch_and_return_cleanup(self):
        self.run_source('fn value() -> int { try { return 7 } catch err { return 8 } }\nfn main() { print(value()); try { try { print(1 / 0) } catch first { print(2 / 0) } } catch second { print(second.code) } }', [7, 'numeric.division_by_zero'])

    def test_fatal_errors_bypass_catch(self):
        result = self.lower('fn main() { try { assert(false) } catch err { print("wrong") } }', "bytecode")
        self.assertEqual(result['diagnostics'], [])
        vm = VM(result['bytecode'])
        with self.assertRaisesRegex(PixelError, 'Assertion failed'):
            vm.run()
        self.assertEqual(vm.output, [])

    def test_enum_construction_and_match_expressions(self):
        self.run_source('enum Choice[T] { Value(item: T), Empty }\nfn main() { let a = Choice[int].Value(7); let b = Choice[int].Empty; print(match a { Choice[int].Value(item) => item + 1, Choice[int].Empty => 0 }); print(match b { Choice[int].Value(item) => item, _ => 9 }); let value: Option[string] = Some("x"); print(match value { Some(text) => text, None => "none" }); let missing: Option[int] = None; print(match missing { Some(number) => number, None => 3 }) }', [8, 9, 'x', 3])

    def test_match_statement_bindings_and_loop_transfers(self):
        self.run_source('fn main() { let callbacks: [fn()->int] = []; for value in [1, 2, 3] { let choice: Option[int] = Some(value); match choice { Some(number) => { if number == 2 { continue }; append(callbacks, fn() = number) }, None => { break } } }; print(callbacks[0]()); print(callbacks[1]()) }', [1, 3])

    def test_decode_records_and_catch_validation_error(self):
        self.run_source('record Data { name: string, values: [int] }\nfn main() { let data = jsonDecode[Data]("{\\\"name\\\":\\\"ok\\\",\\\"values\\\":[1,2]}"); print(data.name); print(data.values); try { let invalid = jsonDecode[Data]("{}"); print(invalid) } catch err { print(err.kind) } }', ['ok', [1, 2], 'json'])

    def test_binary_data_builtins_compile_without_host_frontend(self):
        self.run_source('fn main() { let bytes = utf8Encode("界"); print(bytes); print(utf8Decode(bytes)); print(float64Bytes(-0.0)); print(float64FromBytes(float64Bytes(1.5))) }', [[231, 149, 140], '界', [128, 0, 0, 0, 0, 0, 0, 0], 1.5])

    def test_real_data_processing_projects(self):
        library = {'std/' + p.name: p.read_text() for p in (ROOT / 'pixellang' / 'stdlib').glob('*.pxl')}
        for name in ('callback-analysis', 'ledger', 'log-analysis'):
            with self.subTest(project=name), tempfile.TemporaryDirectory() as directory:
                root = ROOT / 'examples' / name
                files = {p.name: p.read_text() for p in root.glob('*.pxl')}
                files.update(library)
                result = self.lower(files, 'bytecode')
                self.assertEqual(result['diagnostics'], [])
                fixtures = root if name == 'callback-analysis' else root / 'fixtures'
                request = fixtures / ('input.json' if name == 'callback-analysis' else 'config.json')
                expected = json.loads((fixtures / 'expected.json').read_text())
                vm = VM(result['bytecode'], input_text=request.read_text(), max_steps=10_000_000,
                        file_access=FileAccess(fixtures, Path(directory)))
                self.assertEqual(vm.run(), expected)
                if name != 'callback-analysis':
                    report = Path(directory) / json.loads(request.read_text())['output']
                    self.assertEqual(json.loads(report.read_text()), expected[0])

    def test_compiler_source_lowers_to_ir(self):
        vm = VM(self.bytecode, **SELFHOST_HEAP_LIMITS, input_text=json.dumps({
            'source': 'main.pxl', 'text': '', 'operation': 'ir-summary', 'files': self.files,
        }), max_steps=1_000_000_000, max_depth=1024)
        with patch('pixellang.text.parse_text', side_effect=AssertionError('delegated')), patch(
            'pixellang.semantics.Checker.check', side_effect=AssertionError('delegated')
        ):
            result = vm.run()[0]
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(result['unresolved'], 0)
        self.assertGreater(result['functions'], 100)
        self.assertGreater(result['instructions'], 1000)

    def test_unsupported_statement_has_no_executable_functions(self):
        result = self.lower('fn main() { fn unused() {} }')
        self.assertEqual(result['diagnostics'][0]['phase'], 'ir')
        self.assertIn('Nested named-function', result['diagnostics'][0]['message'])
        self.assertEqual(result['ir']['functions'], {})
        self.assertEqual(result['ir']['entry'], [])

    def test_underscore_variant_is_not_a_wildcard(self):
        self.run_source('''enum Plain { _, Other }
enum Choice[T] { _(value: T), Other }
fn main() {
 let plain = Plain.Other
 print(match plain { Plain._ => 1, Plain.Other => 2 })
 let empty = Choice[int].Other
 print(match empty { Choice[int]._(n) => n, Choice[int].Other => 7 })
 let filled = Choice[int]._(9)
 print(match filled { Choice[int]._(n) => n, _ => 0 })
 match filled { Choice[int]._(n) => { print(n + 3) }, _ => { print(0) } }
}''', [2, 7, 9, 12])
