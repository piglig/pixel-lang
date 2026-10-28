"""Capture identities and per-iteration values through the native closure planner."""
import json
import unittest

from pixellang.bootstrap import read_bytecode_stream
from pixellang.code_image import CodeImage
from pixellang.vm import VM
from tests.native_compiler import load_native_compiler
from tests.selfhost_runtime import SELFHOST_HEAP_LIMITS


class CaptureAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CodeImage(load_native_compiler(driver=None))

    def request(self, source, operation):
        return VM(self.compiler, **SELFHOST_HEAP_LIMITS, input_text=json.dumps(dict(
            source='main.pxl', text=source, files={'main.pxl': source}, operation=operation)),
            max_steps=20_000_000).run()

    def plan(self, source):
        result = self.request(source, 'closures')[0]
        self.assertEqual(result['diagnostics'], [])
        return result

    def captures(self, plan):
        return [[slot['symbol'] for slot in closure['captures']] for closure in plan['closures']]

    def test_siblings_capture_readonly_values_from_same_binding(self):
        plan = self.plan('''effect fn main() {
 var count = 0
 let increment = fn() -> int { return count + 1 }
 let read = fn() -> int { return count }
 count = 9
 print(increment()); print(read())
}''')
        count = next(i for i, symbol in enumerate(plan['symbols']) if symbol['name'] == 'count')
        self.assertTrue(plan['symbols'][count]['mutable'])
        self.assertEqual(self.captures(plan), [[count], [count]])
        self.assertTrue(all(not c['captures'][0]['mutable'] for c in plan['closures']))
        rejected = self.request('fn main(){var count=0;let change=fn(){count+=1}}', 'check')[0]
        self.assertTrue(any('Captured values are readonly' in d['message'] for d in rejected['diagnostics']))

    def test_nested_capture_propagates_through_intermediate_function(self):
        plan = self.plan('''effect fn main() {
 let value = 42
 let outer = fn() -> fn() -> int { return fn() -> int { return value } }
 print(outer()())
}''')
        value = next(i for i, symbol in enumerate(plan['symbols']) if symbol['name'] == 'value')
        self.assertFalse(plan['symbols'][value]['mutable'])
        self.assertEqual(self.captures(plan), [[value], [value]])
        root = next(i for i, c in enumerate(plan['closures']) if c['parent'] == -1)
        self.assertEqual([c['parent'] for c in plan['closures'] if c['parent'] != -1], [root])

    def test_shadowed_bindings_and_initializers(self):
        plan = self.plan('''effect fn main() {
 let value = 1
 let outer = fn() -> int {
  let value = value + 1
  let inner = fn() -> int { return value }
  return inner()
 }
 print(outer())
}''')
        values = [i for i, symbol in enumerate(plan['symbols']) if symbol['name'] == 'value']
        self.assertEqual(len(values), 2)
        self.assertNotEqual(*values)
        outer = next(c for c in plan['closures'] if c['parent'] == -1)
        inner = next(c for c in plan['closures'] if c['parent'] != -1)
        self.assertEqual([c['symbol'] for c in outer['captures']], [values[0]])
        self.assertEqual([c['symbol'] for c in inner['captures']], [values[1]])

    def test_loop_binding_declaration_is_retained_for_fresh_allocation(self):
        source = '''effect fn main() {
 var functions: [fn() -> int] = []
 for index, value in [10, 20] {
  functions.append(fn() -> int { return index + value })
 }
 print(functions[0]()); print(functions[1]())
}'''
        plan = self.plan(source)
        captures = [plan['symbols'][i] for i in self.captures(plan)[0]]
        self.assertEqual([symbol['name'] for symbol in captures], ['index', 'value'])
        self.assertTrue(all(not symbol['mutable'] for symbol in captures))
        self.assertNotEqual(captures[0]['node'], captures[1]['node'])
        # Each iteration must capture fresh bindings; shared cells would print 21 twice.
        result = read_bytecode_stream(self.request(source, 'bytecode-stream'))
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(VM(result['bytecode']).run(), [10, 21])

    def test_capture_function_parameter_used_as_call_target(self):
        plan = self.plan('''fn wrap(operation: fn(int) -> int) -> fn(int) -> int {
 return fn(value: int) -> int { return operation(value) }
}
effect fn main() { print(wrap(fn(value: int) = value + 1)(2)) }
''')
        wrap = next(c for c in plan['closures'] if c['captures'])
        captures = [plan['symbols'][c['symbol']] for c in wrap['captures']]
        self.assertEqual([symbol['name'] for symbol in captures], ['operation'])
        self.assertEqual(captures[0]['kind'], 'parameter')

    def test_option_and_result_bindings_have_local_capture_identity(self):
        plan = self.plan('''effect fn main() {
 match Option.Some(1) {
  Option.Some(value) => { let read = fn() -> int { return value }; print(read()) }
  Option.None => {}
 }
 match parseInt("bad") { .Ok(number) => {}, .Err(error) => { let read = fn() -> string { return error.message }; print(read()) } }
}''')
        captures = self.captures(plan)
        self.assertEqual([[plan['symbols'][i]['name'] for i in group] for group in captures],
                         [['value'], ['error']])
        self.assertNotEqual(captures[0][0], captures[1][0])

    def test_anonymous_signature_rejects_invalid_names_and_missing_body(self):
        for expression, phase in [('fn(1) {}', 'parse'), ('fn(int) {}', 'check'), ('fn() -> int', 'parse')]:
            with self.subTest(expression=expression):
                result = self.request('effect fn main() { let value = ' + expression + ' }', 'check')[0]
                self.assertTrue(result['diagnostics'])
                self.assertTrue(any(item['phase'] == phase for item in result['diagnostics']))


if __name__ == '__main__':
    unittest.main()
