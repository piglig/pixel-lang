"""Language cases shared by pixel AST and PNG source-recovery execution.

Adapters implement assert_execution(source, expected) and return a pixel document.
Backend-specific checks stay in their owning test class.
"""


class PixelLanguageCases:
    def test_scalar_expressions_and_recursive_function(self):
        self.assert_execution('fn fact(n: int) -> int { if n <= 1 { return 1 }; return n * fact(n - 1) }\neffect fn main() { print(fact(6)); print(-9223372036854775808); print("界😀"); print(-2.5); print(!false) }', [720, -9223372036854775808, '界😀', -2.5, True])

    def test_shadowing_and_control_flow(self):
        self.assert_execution('effect fn main() { var total = 0; var i = 0; while i < 5 { i += 1; if i == 2 { continue } else if i == 4 { break }; total += i }; if total == 4 { let total = "ok"; print(total) }; print(total) }', ['ok', 4])

    def test_collections_iteration_and_mutation(self):
        self.assert_execution('effect fn main() { var values: [int] = [1, 2]; for index, value in values { values[index] += 1; print(value) }; print(values); var data: map[string, int] = map[string, int]{"x": 3}; data["x"] = 4; print(data["x"]); print(len(values)) }', [1, 2, [2, 3], 4, 2])

    def test_short_circuit_and_explicit_failure_results(self):
        self.assert_execution('effect fn mark() -> bool { print("effect"); return true }\neffect fn main() { print(false && mark()); print(true || mark()); match parseInt("invalid") { .Ok(n) => { assert(false) }, .Err(error) => { print(error.code) } } }', [False, True, 'numeric.invalid_text'])

    def test_function_values_and_typed_empty_array(self):
        self.assert_execution('fn inc(value: int) -> int = value + 1\neffect fn main() { let f = inc; var values: [int] = []; values.append(f(4)); print(values) }', [[5]])

    def test_record_construction_fields_and_json_decode(self):
        self.assert_execution('record R { value: int, names: [string] }\nfn add(item: inout R) -> int { item.value += 2; return item.value }\neffect fn main() { let value = 1; var item: R = R{value, names: ["x"]}; print(add(&item)); print(item.names[0]); let decoded = jsonDecode::[R]("{\\\"value\\\":4,\\\"names\\\":[]}"); match decoded { .Ok(row) => { print(row.value) }, .Err(error) => { assert(false) } } }', [3, 'x', 4])

    def test_generic_functions_records_and_function_values(self):
        self.assert_execution('record Box[T] { value: T }\nfn identity[T](value: T) -> T = value\nfn boxed[T](value: T) -> Box[T] { return Box::[T]{value: identity(value)} }\neffect fn main() { let a = boxed(3); let b = boxed("x"); let f = identity::[int]; print(a.value); print(b.value); print(f(7)); print(identity::[string]("ok")) }', [3, 'x', 7, 'ok'])

    def test_distinct_type_parameter_identities(self):
        self.assert_execution('record First[T] { value: T }\nrecord Second[T] { value: T }\neffect fn pair[T, U](left: T, right: U) -> U { let a: First[T] = First::[T]{value: left}; let b: Second[U] = Second::[U]{value: right}; print(a.value); return b.value }\neffect fn main() { print(pair(4, "x")); print(pair("y", 5)) }', [4, 'x', 'y', 5])

    def test_generic_enum_and_option_match_expressions(self):
        self.assert_execution('enum Choice[T] { Value(item: T), Empty }\nfn read[T](choice: Choice[T], fallback: T) -> T { return match choice { Choice[T].Value(item) => item, Choice[T].Empty => fallback } }\neffect fn main() { print(read(Choice::[int].Value(8), 0)); print(read(Choice::[string].Empty, "empty")); let value: Option[int] = Option.Some(3); print(match value { Option.Some(number) => number + 1, Option.None => 0 }); let missing: Option[string] = Option.None; print(match missing { Option.Some(text) => text, _ => "none" }) }', [8, 'empty', 4, 'none'])

    def test_statement_match_and_loop_transfers(self):
        self.assert_execution('effect fn main() { for value in [1, 2, 3] { let choice: Option[int] = Option.Some(value); match choice { Option.Some(number) => { if number == 2 { continue }; print(number) }, Option.None => { break } } } }', [1, 3])

    def test_lambda_parameters_and_immediate_invocation(self):
        self.assert_execution('effect fn main() { let add: fn(int)->int = fn(value: int) = value + 1; print(add(4)); print((fn() = 7)()) }', [5, 7])

    def test_value_and_nested_closure_captures(self):
        self.assert_execution('effect fn main() { var count = 1; let read = fn() = count; let outer = fn() -> fn()->int { return fn() = count }; count = 9; print(read()); print(outer()()); print(count) }', [1, 1, 9])

    def test_generic_closure_and_fresh_iteration_bindings(self):
        self.assert_execution('fn make[T](value: T) -> fn()->T { return fn() = value }\neffect fn main() { let a = make(3); let b = make("x"); print(a()); print(b()); var callbacks: [fn()->int] = []; for index, value in [10, 20, 30] { callbacks.append(fn() = index + value) }; print(callbacks[0]()); print(callbacks[2]()) }', [3, 'x', 10, 32])

    def test_pattern_capture_escapes_arm(self):
        self.assert_execution('effect fn main() { let choice: Option[int] = Option.Some(8); let callback = match choice { Option.Some(value) => fn() = value, Option.None => fn() = 0 }; print(callback()) }', [8])

    def test_multifile_generic_functions_and_closures(self):
        source = self.assert_execution({
            'main.pxl': 'import "lib.pxl" as lib\neffect fn main() { print(lib.identity(4)); let f = lib.identity::[string]; print(f("ok")); print(lib.make(9)()) }',
            'lib.pxl': 'export fn identity[T](value: T) -> T = value\nexport fn make[T](value: T) -> fn()->T { return fn() = value }',
        }, [4, 'ok', 9])
        self.assertEqual(len(source['bundle']['modules']), 1)
        self.assertTrue(all(item['metadata'] == {} for item in source['bundle']['modules'].values()))

    def test_shared_transitive_module_nominal_types(self):
        self.assert_execution({
            'main.pxl': 'import "types.pxl" as types\nimport "worker.pxl" as work\neffect fn main() { let box = types.Box::[int]{value: 5}; print(work.read(box)); let value = types.Choice::[int].Value(8); print(match value { types.Choice[int].Value(number) => number, types.Choice[int].Empty => 0 }) }',
            'types.pxl': 'export record Box[T] { value: T }\nexport enum Choice[T] { Value(item: T), Empty }',
            'worker.pxl': 'import "types.pxl" as types\nexport fn read(box: types.Box[int]) -> int { return box.value }',
        }, [5, 8])

    def test_multiple_aliases_share_one_module_import(self):
        self.assert_execution({
            'main.pxl': 'import "lib.pxl" as first\nimport "lib.pxl" as second\neffect fn main() { print(first.value()); print(second.value()) }',
            'lib.pxl': 'export fn value() -> int = 7',
        }, [7, 7])
