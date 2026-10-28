import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from tests.native_compiler import compile_native_sources, execute_native_driver
from pixellang.model import PixelError
from pixellang.fileaccess import FileAccess
from pixellang.vm import VM
from tests.selfhost_runtime import SELFHOST_HEAP_LIMITS

ROOT = Path(__file__).resolve().parents[1]


class SelfHostedIRTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = {p.name: p.read_text() for p in (ROOT / 'selfhost').glob('*.pxl')}
        cls.bytecode = compile_native_sources(cls.files)

    def lower(self, files, operation="ir"):
        if isinstance(files, str):
            files = {'main.pxl': files}
        vm = VM(self.bytecode, **SELFHOST_HEAP_LIMITS, input_text=json.dumps({
            'source': 'main.pxl', 'text': '', 'operation': operation, 'files': files,
        }), max_steps=100_000_000, max_depth=1024)
        with patch('pixellang.text.parse_text', side_effect=AssertionError('delegated')):
            return json.loads(vm.run()[0])

    def run_source(self, files, expected):
        result = self.lower(files, "bytecode")
        self.assertEqual(result['diagnostics'], [])
        bytecode = result['bytecode']
        with patch('pixellang.text.parse_text', side_effect=AssertionError('delegated')):
            self.assertEqual(VM(bytecode, max_steps=1_000_000).run(), expected)
        return bytecode

    def test_arithmetic_and_integer_boundary(self):
        self.run_source('effect fn main() { print(2 + 3 * 4); print(-9223372036854775808); print("a" + "b"); print(1.5 + 2.25) }', [14, -9223372036854775808, 'ab', 3.75])

    def test_loop_break_continue_and_shadowing(self):
        self.run_source('effect fn main() { var i = 0; var total = 0; while i < 10 { i += 1; if i == 2 { continue }; if i == 5 { break }; total += i }; if total == 8 { let total = "ok"; print(total) } else { print("bad") }; print(total) }', ['ok', 8])

    def test_short_circuit_skips_effects(self):
        self.run_source('effect fn markEffect() -> bool { print("effect"); return true }\neffect fn main() { print(false && markEffect()); print(true || markEffect()); print(true && markEffect()) }', [False, True, 'effect', True])

    def test_generic_calls_recursion_and_modules(self):
        self.run_source({
            'main.pxl': 'import "lib.pxl" as lib\neffect fn main() { print(lib.identity(lib.fact(6))); print(lib.identity("x")) }',
            'lib.pxl': 'export fn identity[T](value: T) -> T { return value }\nexport fn fact(n: int) -> int { if n <= 1 { return 1 }; return n * fact(n - 1) }',
        }, [720, 'x'])

    def test_static_calls_avoid_callable_allocation_and_keep_argument_order(self):
        code = self.run_source('''
effect fn mark(value: int) -> int { print(value); return value }
fn sum(left: int, right: int) -> int = left + right
effect fn main() { print(sum(mark(1), mark(2))) }
''', [1, 2, 3])
        instructions = [instruction for function in code['functions'].values()
                        for instruction in function['instructions']]
        self.assertEqual(sum(i['op'] == 'CALL' for i in instructions), 3)
        self.assertFalse(any(i['op'] in ('FUNCTION', 'CLOSURE', 'INVOKE') for i in instructions))

    def test_arrays_records_and_shorthand(self):
        self.run_source('record Box[T] { value: T }\neffect fn main() { let value = [3, 5]; let box = Box::[[int]]{value}; print(box.value[1]); print(len(value)) }', [5, 2])

    def test_readonly_value_captures_and_sibling_closures(self):
        self.run_source('effect fn main(){var count=2;let read=fn()=count;let next=fn()=count+1;count=9;print(read());print(next());print(count)}', [2,3,9])
        self.run_source('fn make[T](value:T)->fn()->T{return fn()=value}\neffect fn main(){let a=make(3);let b=make("x");print(a());print(b())}', [3,'x'])
        result=self.lower('fn main(){var count=0;let increment=fn(){count+=1};increment()}', 'bytecode')
        self.assertTrue(result['diagnostics'])

    def test_nested_closure_and_fresh_while_binding(self):
        self.run_source('effect fn main() { var i = 0; var callbacks: [fn()->int] = []; while i < 3 { let value = i; callbacks.append(fn() = value); i += 1 }; print(callbacks[0]()); print(callbacks[2]()); var x = 7; let outer = fn() -> fn()->int { return fn() = x }; print(outer()()) }', [0, 2, 7])

    def test_callable_is_evaluated_before_arguments(self):
        self.run_source('fn change(target:inout fn(int)->int)->int{target=fn(x:int)=x+100;return 5}\neffect fn main(){var f=fn(x:int)=x+1;print(f(change(&f)));print(f(5))}', [6,105])

    def test_mutation_and_sorting(self):
        self.run_source('record Box { value: int }\neffect fn main() { var box: Box = Box{value: 1}; box.value += 2; print(box.value); var values: [int] = [4, 1, 3, 2]; var i = 0; while i < len(values) { var j = i + 1; while j < len(values) { if values[j] < values[i] { let temp = values[i]; values[i] = values[j]; values[j] = temp }; j += 1 }; i += 1 }; values[0] += 1; print(values) }', [3, [2, 2, 3, 4]])

    def test_maps_and_runtime_error_provenance(self):
        self.run_source('effect fn main() { var counts: map[string, int] = map[string, int]{"a": 1}; counts["a"] += 2; counts["b"] = 4; print(counts["a"]); print(len(counts)) }', [3, 2])
        source = 'effect fn main() {\n    print(1 / 0)\n}'
        result = self.lower(source, "bytecode")
        self.assertEqual(result['diagnostics'], [])
        with self.assertRaises(PixelError) as caught:
            VM(result['bytecode']).run()
        span = caught.exception.to_dict()['span']
        self.assertEqual(span['source'], 'main.pxl')
        self.assertEqual(span['start'], {'line': 2, 'column': 11, 'offset': source.index('1 / 0')})

    def test_for_array_string_and_map_snapshots(self):
        self.run_source('effect fn main() { var values: [int] = [1, 2]; for index, value in values { print(index); print(value); values[1] = 9; values.append(3) }; for index, char in "a界" { print(index); print(char) }; var data: map[string, int] = map[string, int]{"x": 1, "y": 2}; for key, value in data { print(key); print(value); data["y"] = 9; data["z"] = 3 }; for value in ([]: [int]) { print(value) } }', [0, 1, 1, 2, 0, 'a', 1, '界', 'x', 1, 'y', 2])

    def test_for_source_runs_once_and_single_map_yields_keys(self):
        self.run_source('effect fn values() -> [int] { print("source"); return [1, 2, 3] }\neffect fn main() { for value in values() { if value == 2 { continue }; print(value) }; for key in map[string, int]{"a": 7, "b": 8} { print(key) } }', ['source', 1, 3, 'a', 'b'])

    def test_for_closures_have_fresh_pair_bindings(self):
        self.run_source('effect fn main() { var callbacks: [fn()->int] = []; for index, value in [10, 20, 30] { callbacks.append(fn() = index + value) }; print(callbacks[0]()); print(callbacks[2]()) }', [10, 32])

    def test_nested_for_temporaries_and_value_snapshot(self):
        ir=self.run_source('record Item{value:int}\neffect fn main(){var item=Item{value:1};let values=[item,item];var sum=0;for value in values{var copy=value;copy.value+=1;print(copy.value);for number in [1,2]{sum+=number}};print(sum);print(item.value)}', [2,2,6,1])
        main=ir['functions'][ir['entry'][0]]
        self.assertGreaterEqual(main['locals'],9)

    def test_local_loop_transfers_preserve_pending_accesses(self):
        self.run_source('fn add(value:inout int,amount:int){value+=amount}\neffect fn main(){var total=10;for n in [1,2,3,4]{if n==2{continue};if n==4{break};let choice:Option[int]=.Some(n);add(&total,match choice{Option.Some(x)=>{while true{break};x},Option.None=>0})};print(total)}', [14])

    def test_result_propagation_and_return_cleanup(self):
        self.run_source('fn value(text:string)->Result[int,Error]{let number=1+parseInt(text)?;return .Ok(number)}\neffect fn main(){match value("6"){.Ok(n)=>{print(n)},.Err(e)=>{print(e.code)}};match value("bad"){.Ok(n)=>{print(n)},.Err(e)=>{print(e.code)}}}', [7,'numeric.invalid_text'])

    def test_execution_faults_do_not_become_result_errors(self):
        result=self.lower('effect fn main(){match parseInt("1"){.Ok(n)=>{assert(false)},.Err(error)=>{print("wrong")}}}', 'bytecode')
        self.assertEqual(result['diagnostics'],[])
        vm=VM(result['bytecode'])
        with self.assertRaisesRegex(PixelError,'Assertion failed'):vm.run()
        self.assertEqual(vm.output,[])

    def test_enum_construction_and_match_expressions(self):
        self.run_source('enum Choice[T] { Value(item: T), Empty }\neffect fn main() { let a = Choice::[int].Value(7); let b = Choice::[int].Empty; print(match a { Choice[int].Value(item) => item + 1, Choice[int].Empty => 0 }); print(match b { Choice[int].Value(item) => item, _ => 9 }); let value: Option[string] = Option.Some("x"); print(match value { Option.Some(text) => text, Option.None => "none" }); let missing: Option[int] = Option.None; print(match missing { Option.Some(number) => number, Option.None => 3 }) }', [8, 9, 'x', 3])

    def test_match_statement_bindings_and_loop_transfers(self):
        self.run_source('effect fn main() { var callbacks: [fn()->int] = []; for value in [1, 2, 3] { let choice: Option[int] = Option.Some(value); match choice { Option.Some(number) => { if number == 2 { continue }; callbacks.append(fn() = number) }, Option.None => { break } } }; print(callbacks[0]()); print(callbacks[1]()) }', [1, 3])

    def test_decode_records_and_explicit_validation_result(self):
        source='''record Data{name:string,values:[int]}
effect fn main(){
 match jsonDecode::[Data](jsonFrom(Data{name:"ok",values:[1,2]})){
  .Ok(data)=>{print(data.name);print(data.values)},.Err(error)=>{assert(false)}
 }
 match jsonDecode::[Data]("{}"){.Ok(data)=>{assert(false)},.Err(error)=>{print(error.kind)}}
}'''
        self.run_source(source,['ok',[1,2],'json'])

    def test_binary_data_builtins_compile_without_host_frontend(self):
        self.run_source('effect fn main(){let bytes=utf8Encode("界");print(bytes);match utf8Decode(bytes){.Ok(value)=>{print(value)},.Err(error)=>{assert(false)}};print(float64Bytes(-0.0));match float64FromBytes(float64Bytes(1.5)){.Ok(value)=>{print(value)},.Err(error)=>{assert(false)}}}', [[231,149,140],'界',[128,0,0,0,0,0,0,0],1.5])

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

    def test_nested_record_value_isolation_without_freeze_thaw(self):
        code=self.run_source('record Box{values:[int]}\neffect fn main(){var source=Box{values:[1,2]};let snapshot=source;source.values[0]=9;var copy=snapshot;copy.values[1]=7;print(source.values);print(snapshot.values);print(copy.values)}', [[9,2],[1,2],[1,7]])
        ops={instruction['op'] for function in code['functions'].values() for instruction in function['instructions']}
        self.assertNotIn('FREEZE',ops)
        self.assertNotIn('THAW',ops)
        self.assertTrue(self.lower('fn main(){let value=freeze([1])}','bytecode')['diagnostics'])

    def test_compiler_source_lowers_to_ir(self):
        request = dict(source='main.pxl', text='', operation='ir-summary', files=self.files)
        with patch('pixellang.text.parse_text', side_effect=AssertionError('delegated')):
            result = execute_native_driver(self.bytecode, request)[0]
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(result['unresolved'], 0)
        self.assertGreater(result['functions'], 100)
        self.assertGreater(result['instructions'], 1000)

    def test_native_ir_summary_matches_vm(self):
        for source in ('fn main(){let value=2+3}', 'fn main(){let value=missing}'):
            with self.subTest(source=source):
                request = dict(source='main.pxl', text='', operation='ir-summary',
                               files={'main.pxl': source})
                expected = VM(self.bytecode, **SELFHOST_HEAP_LIMITS,
                              input_text=json.dumps(request), max_steps=30_000_000,
                              max_depth=1024).run()[0]
                self.assertEqual(execute_native_driver(self.bytecode, request)[0], expected)

    def test_invalid_nested_declaration_has_no_executable_functions(self):
        result = self.lower('effect fn main() { fn unused() {} }')
        self.assertEqual(result['diagnostics'][0]['phase'], 'check')
        self.assertIn('module level', result['diagnostics'][0]['message'])
        self.assertEqual(result['ir']['functions'], {})
        self.assertEqual(result['ir']['entry'], [])

    def test_underscore_variant_is_not_a_wildcard(self):
        self.run_source('''enum Plain { _, Other }
enum Choice[T] { _(value: T), Other }
effect fn main() {
 let plain = Plain.Other
 print(match plain { Plain._ => 1, Plain.Other => 2 })
 let empty = Choice::[int].Other
 print(match empty { Choice[int]._(n) => n, Choice[int].Other => 7 })
 let filled = Choice::[int]._(9)
 print(match filled { Choice[int]._(n) => n, _ => 0 })
 match filled { Choice[int]._(n) => { print(n + 3) }, _ => { print(0) } }
}''', [2, 7, 9, 12])
