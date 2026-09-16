import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from pixellang.compiler import compile_source
from pixellang.fileaccess import FileAccess
from pixellang.project import compile_project
from pixellang.vm import VM
from tests.selfhost_runtime import SELFHOST_HEAP_LIMITS

ROOT = Path(__file__).resolve().parents[1]


class SelfHostedPixelASTTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bytecode = compile_project({p.name: p.read_text() for p in (ROOT / 'selfhost').glob('*.pxl')})[1].bytecode

    def encode(self, source):
        files = source if isinstance(source, dict) else {'main.pxl': source}
        vm = VM(self.bytecode, **SELFHOST_HEAP_LIMITS, input_text=json.dumps({
            'source': 'main.pxl', 'text': '', 'operation': 'pixels', 'files': files,
        }), max_steps=100_000_000, max_depth=1024, max_output_chars=64_000_000)
        with patch('pixellang.text.parse_text', side_effect=AssertionError('delegated')), patch(
            'pixellang.semantics.Checker.check', side_effect=AssertionError('delegated')
        ), patch('pixellang.canonical.encode', side_effect=AssertionError('delegated')):
            return vm.run()[0]

    def assert_execution(self, source, expected):
        result = self.encode(source)
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(result['source']['metadata'], {})
        self.assertTrue(result['source']['spatial']['links'])
        self.assertEqual(VM(compile_source(result['source']).bytecode, max_steps=1_000_000).run(), expected)
        return result['source']

    def test_scalar_expressions_and_recursive_function(self):
        self.assert_execution('fn fact(n: int) -> int { if n <= 1 { return 1 }; return n * fact(n - 1) }\nfn main() { print(fact(6)); print(-9223372036854775808); print("界😀"); print(-2.5); print(!false) }', [720, -9223372036854775808, '界😀', -2.5, True])

    def test_shadowing_and_control_flow(self):
        self.assert_execution('fn main() { var total = 0; var i = 0; while i < 5 { i++; if i == 2 { continue } else if i == 4 { break }; total += i }; if total == 4 { let total = "ok"; print(total) }; print(total) }', ['ok', 4])

    def test_collections_iteration_and_mutation(self):
        self.assert_execution('fn main() { let values = [1, 2]; for index, value in values { values[index] += 1; print(value) }; print(values); let data = map[int]{"x": 3}; data["x"] = 4; print(data["x"]); print(len(values)) }', [1, 2, [2, 3], 4, 2])

    def test_short_circuit_and_exception_regions(self):
        self.assert_execution('fn effect() -> bool { print("effect"); return true }\nfn main() { print(false && effect()); print(true || effect()); try { print(1 / 0) } catch err { print("caught") } }', [False, True, 'caught'])

    def test_function_values_and_typed_empty_array(self):
        self.assert_execution('fn inc(value: int) -> int = value + 1\nfn main() { let f = inc; let values: [int] = []; append(values, f(4)); print(values) }', [[5]])

    def test_record_construction_fields_and_json_decode(self):
        self.assert_execution('record R { value: int, names: [string] }\nfn add(item: R) -> int { item.value += 2; return item.value }\nfn main() { let value = 1; let item = R{value, names: ["x"]}; print(add(item)); print(item.names[0]); let decoded = jsonDecode[R]("{\\\"value\\\":4,\\\"names\\\":[]}"); print(decoded.value) }', [3, 'x', 4])

    def test_generic_functions_records_and_function_values(self):
        self.assert_execution('record Box[T] { value: T }\nfn identity[T](value: T) -> T = value\nfn boxed[T](value: T) -> Box[T] { return Box[T]{value: identity(value)} }\nfn main() { let a = boxed(3); let b = boxed("x"); let f = identity[int]; print(a.value); print(b.value); print(f(7)); print(identity[string]("ok")) }', [3, 'x', 7, 'ok'])

    def test_distinct_type_parameter_identities(self):
        self.assert_execution('record First[T] { value: T }\nrecord Second[T] { value: T }\nfn pair[T, U](left: T, right: U) -> U { let a = First[T]{value: left}; let b = Second[U]{value: right}; print(a.value); return b.value }\nfn main() { print(pair(4, "x")); print(pair("y", 5)) }', [4, 'x', 'y', 5])

    def test_generic_enum_and_option_match_expressions(self):
        self.assert_execution('enum Choice[T] { Value(item: T), Empty }\nfn read[T](choice: Choice[T], fallback: T) -> T { return match choice { Choice[T].Value(item) => item, Choice[T].Empty => fallback } }\nfn main() { print(read(Choice.Value(8), 0)); print(read(Choice[string].Empty, "empty")); let value: Option[int] = Some(3); print(match value { Some(number) => number + 1, None => 0 }); let missing: Option[string] = None; print(match missing { Some(text) => text, _ => "none" }) }', [8, 'empty', 4, 'none'])

    def test_statement_match_and_loop_transfers(self):
        self.assert_execution('fn main() { for value in [1, 2, 3] { let choice: Option[int] = Some(value); match choice { Some(number) => { if number == 2 { continue }; print(number) }, None => { break } } } }', [1, 3])

    def test_lambda_parameters_and_immediate_invocation(self):
        self.assert_execution('fn main() { let add: fn(int)->int = fn(value: int) = value + 1; print(add(4)); print((fn() = 7)()) }', [5, 7])

    def test_mutable_and_nested_closure_captures(self):
        self.assert_execution('fn main() { var count = 1; let increment = fn() { count += 1 }; let read = fn() = count; increment(); increment(); print(read()); let outer = fn() -> fn()->int { return fn() = count }; count = 9; print(outer()()) }', [3, 9])

    def test_generic_closure_and_fresh_iteration_bindings(self):
        self.assert_execution('fn make[T](value: T) -> fn()->T { return fn() = value }\nfn main() { let a = make(3); let b = make("x"); print(a()); print(b()); let callbacks: [fn()->int] = []; for index, value in [10, 20, 30] { append(callbacks, fn() = index + value) }; print(callbacks[0]()); print(callbacks[2]()) }', [3, 'x', 10, 32])

    def test_pattern_capture_escapes_arm(self):
        self.assert_execution('fn main() { let choice: Option[int] = Some(8); let callback = match choice { Some(value) => fn() = value, None => fn() = 0 }; print(callback()) }', [8])

    def test_multifile_generic_functions_and_closures(self):
        source = self.assert_execution({
            'main.pxl': 'import "lib.pxl" as lib\nfn main() { print(lib.identity(4)); let f = lib.identity[string]; print(f("ok")); print(lib.make(9)()) }',
            'lib.pxl': 'export fn identity[T](value: T) -> T = value\nexport fn make[T](value: T) -> fn()->T { return fn() = value }',
        }, [4, 'ok', 9])
        self.assertEqual(len(source['bundle']['modules']), 1)
        self.assertTrue(all(item['metadata'] == {} for item in source['bundle']['modules'].values()))

    def test_shared_transitive_module_nominal_types(self):
        self.assert_execution({
            'main.pxl': 'import "types.pxl" as types\nimport "worker.pxl" as work\nfn main() { let box = types.Box[int]{value: 5}; print(work.read(box)); let value = types.Choice[int].Value(8); print(match value { types.Choice[int].Value(number) => number, types.Choice[int].Empty => 0 }) }',
            'types.pxl': 'export record Box[T] { value: T }\nexport enum Choice[T] { Value(item: T), Empty }',
            'worker.pxl': 'import "types.pxl" as types\nexport fn read(box: types.Box[int]) -> int { return box.value }',
        }, [5, 8])

    def test_multiple_aliases_share_one_module_import(self):
        self.assert_execution({
            'main.pxl': 'import "lib.pxl" as first\nimport "lib.pxl" as second\nfn main() { print(first.value()); print(second.value()) }',
            'lib.pxl': 'export fn value() -> int = 7',
        }, [7, 7])

    def test_real_data_processing_projects(self):
        library = {'std/' + p.name: p.read_text() for p in (ROOT / 'pixellang' / 'stdlib').glob('*.pxl')}
        for name in ('callback-analysis', 'ledger', 'log-analysis'):
            with self.subTest(project=name), tempfile.TemporaryDirectory() as directory:
                root = ROOT / 'examples' / name
                files = {p.name: p.read_text() for p in root.glob('*.pxl')}
                files.update(library)
                result = self.encode(files)
                self.assertEqual(result['diagnostics'], [])
                fixtures = root if name == 'callback-analysis' else root / 'fixtures'
                request = fixtures / ('input.json' if name == 'callback-analysis' else 'config.json')
                expected = json.loads((fixtures / 'expected.json').read_text())
                bytecode = compile_source(result['source']).bytecode
                vm = VM(bytecode, input_text=request.read_text(), max_steps=10_000_000,
                        file_access=FileAccess(fixtures, Path(directory)))
                self.assertEqual(vm.run(), expected)
                if name != 'callback-analysis':
                    report = Path(directory) / json.loads(request.read_text())['output']
                    self.assertEqual(json.loads(report.read_text()), expected[0])

    def test_rejected_sources_are_not_executable(self):
        result = self.encode('fn main() { print(missing) }')
        self.assertTrue(result['diagnostics'])
        self.assertEqual(result['source']['pixels'], [])
        result = self.encode({'main.pxl': 'import "lib.pxl" as lib\nfn main() { lib.work() }'})
        self.assertTrue(result['diagnostics'])
        self.assertEqual(result['source']['pixels'], [])
        self.assertEqual(result['source']['bundle']['modules'], {})
