import unittest

from pixellang.captures import analyze_captures
from pixellang.model import PixelError
from pixellang.text import parse_text


class CaptureAnalysisTests(unittest.TestCase):
    def plan(self, source):
        return analyze_captures(parse_text(source))

    def test_siblings_share_mutable_binding_identity(self):
        plan = self.plan("""fn main() {
 var count = 0
 let increment = fn() -> int { count += 1; return count }
 let read = fn() -> int { return count }
}""")
        count = next(b for b in plan.bindings if b.name == "count")
        self.assertTrue(count.mutable)
        self.assertTrue(count.captured)
        self.assertEqual(plan.functions[1].captures, [count.identity])
        self.assertEqual(plan.functions[2].captures, [count.identity])

    def test_nested_capture_propagates_through_intermediate_function(self):
        plan = self.plan("""fn main() {
 let value = 42
 let outer = fn() -> fn() -> int {
  return fn() -> int { return value }
 }
}""")
        value = next(b for b in plan.bindings if b.name == "value")
        self.assertFalse(value.mutable)
        self.assertEqual(plan.functions[1].captures, [value.identity])
        self.assertEqual(plan.functions[2].captures, [value.identity])

    def test_shadowed_bindings_and_initializers(self):
        plan = self.plan("""fn main() {
 let value = 1
 let outer = fn() -> int {
  let value = value + 1
  let inner = fn() -> int { return value }
  return inner()
 }
}""")
        values = [b for b in plan.bindings if b.name == "value"]
        self.assertEqual(len(values), 2)
        self.assertNotEqual(values[0].identity, values[1].identity)
        self.assertEqual(plan.functions[1].captures, [values[0].identity])
        self.assertEqual(plan.functions[2].captures, [values[1].identity])

    def test_loop_binding_declaration_is_retained_for_fresh_allocation(self):
        plan = self.plan("""fn main() {
 let functions: [fn() -> int] = []
 for index, value in [10, 20] {
  append(functions, fn() -> int { return index + value })
 }
}""")
        captures = [plan.bindings[i] for i in plan.functions[1].captures]
        self.assertEqual([b.name for b in captures], ["index", "value"])
        self.assertTrue(all(b.declaration.kind == "for" for b in captures))
        self.assertTrue(all(not b.mutable for b in captures))

    def test_capture_function_parameter_used_as_call_target(self):
        plan = self.plan("""fn wrap(operation: fn(int) -> int) -> fn(int) -> int {
 return fn(value: int) -> int { return operation(value) }
}""")
        self.assertEqual(
            [plan.bindings[i].name for i in plan.functions[1].captures], ["operation"]
        )
        self.assertIsNotNone(plan.bindings[0].parameter)

    def test_case_and_catch_bindings_have_local_capture_identity(self):
        plan = self.plan("""fn main() {
 match Some(1) {
  Some(value) => { let read = fn() -> int { return value } }
  None => {}
 }
 try { fail("bad") }
 catch error { let read = fn() -> string { return error.message } }
}""")
        self.assertEqual(
            [plan.bindings[i].name for i in plan.functions[1].captures], ["value"]
        )
        self.assertEqual(
            [plan.bindings[i].name for i in plan.functions[2].captures], ["error"]
        )

    def test_anonymous_signature_rejects_invalid_names_and_missing_body(self):
        for expression in [
            "fn(1) {}",
            "fn(int) {}",
            "fn() -> int",
        ]:
            with self.subTest(expression=expression), self.assertRaises(PixelError):
                parse_text("fn main() { let value = " + expression + " }")


if __name__ == "__main__":
    unittest.main()
