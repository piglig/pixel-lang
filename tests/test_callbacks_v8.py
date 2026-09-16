import unittest

from pixellang.compiler import compile_source
from pixellang.editor import SymbolIndex, complete, format_source
from pixellang.model import PixelError
from pixellang.picture import decode_picture, encode_picture
from pixellang.project import compile_project, project_text
from pixellang.vm import VM


class ConciseCallbackTests(unittest.TestCase):
    def test_expression_callbacks_capture_and_compose_across_modules(self):
        files = {
            "main.pxl": """import "std/collections.pxl" as collections
fn main() {
 let minimum = 2
 let chosen = collections.Filter([3, 1, 2], fn(value) = value >= minimum)
 let sorted = collections.Sort(chosen, fn(a, b) = a < b)
 print(collections.Map(sorted, fn(value) = string(value)))
 let make = fn(offset: int) -> fn(int) -> int = fn(value: int) -> int = value + offset
 print(make(10)(5))
}"""
        }
        source, compiled = compile_project(files)
        expected = [["2", "3"], 15]
        self.assertEqual(VM(compiled.bytecode).run(), expected)
        image = decode_picture(encode_picture(source))
        self.assertEqual(VM(compile_source(image).bytecode).run(), expected)
        recovered = project_text(image)
        self.assertEqual(
            VM(compile_project(recovered["files"])[1].bytecode).run(), expected
        )
        formatted = format_source(files["main.pxl"], "main.pxl")
        self.assertEqual(format_source(formatted, "main.pxl"), formatted)
        self.assertEqual(
            VM(compile_project({"main.pxl": formatted})[1].bytecode).run(), expected
        )

    def test_expression_callback_return_type_is_checked(self):
        with self.assertRaisesRegex(PixelError, "Expected int, got bool"):
            compile_project(
                {
                    "main.pxl": "fn main() { let f = fn(x: int) -> int = x > 0; print(f(1)) }"
                }
            )

    def test_contextual_blocks_and_dependency_order(self):
        code = """import "std/collections.pxl" as collections
fn apply[T, U](f: fn(T) -> U, value: T) -> U = f(value)
fn choose[T](empty: [T], value: T) -> T = value
fn main() {
 let increment: fn(int) -> int = fn(x) { return x + 1 }
 print(increment(4))
 print(apply(fn(x) = string(x), 7))
 print(choose([], apply(fn(x) = x + 1, 8)))
 print(collections.Map([1, 2], fn(x) { if x > 1 { return "big" }; return "small" }))
 let constant = fn() = 42
 print(constant())
}
"""
        self.assertEqual(
            VM(compile_project({"main.pxl": code})[1].bytecode).run(),
            [5, "7", 9, ["small", "big"], 42],
        )

    def test_ambiguity_and_inconsistent_inferred_returns(self):
        cases = [
            ("fn main() { let f = fn(x) = x }", "Cannot infer anonymous parameter"),
            (
                "fn main() { let f = fn(x: bool) { if x { return 1 }; return false } }",
                "Expected int, got bool",
            ),
            (
                "fn main() { let f = fn(x: bool) { if x { return 1 } } }",
                "return a value on every path",
            ),
            ("fn main() { let f: fn(int) -> int = fn(x, y) = x }", "parameter count"),
        ]
        for code, error in cases:
            with self.subTest(code=code), self.assertRaisesRegex(PixelError, error):
                compile_project({"main.pxl": code})

    def test_contextual_parameter_completion_and_rename(self):
        code = """import "std/collections.pxl" as collections
record Row { amount: int }
fn main() {
 let rows = [Row{amount: 1}]
 let values = collections.Map(rows, fn(row) = row.amount)
 print(values)
}
"""
        index = SymbolIndex({"main.pxl": code})
        self.assertEqual(
            len(index.rename("main.pxl", code.index("fn(row)") + 3, "item")), 2
        )
        incomplete = code.replace("row.amount", "row.")
        completions = complete(
            {"main.pxl": incomplete},
            "main.pxl",
            "main.pxl",
            incomplete.index("row.") + 4,
        )
        self.assertTrue(
            any(c["label"] == "amount" and c["detail"] == "int" for c in completions),
            completions,
        )

    def test_expected_results_constrain_empty_arguments(self):
        code = """fn identity[T](value: T) -> T = value
fn empty[T]() -> [T] { return [] }
fn main() {
 let values: [int] = identity([])
 let nested: [[string]] = identity([[]])
 let absent: Option[int] = identity(None)
 let more: [int] = empty()
 print(len(values)); print(nested); print(len(more))
 print(match absent { None => true, Some(value) => false })
}
"""
        source, compiled = compile_project({"main.pxl": code})
        expected = [0, [[]], 0, True]
        self.assertEqual(VM(compiled.bytecode).run(), expected)
        self.assertEqual(
            VM(compile_source(decode_picture(encode_picture(source))).bytecode).run(),
            expected,
        )
        with self.assertRaisesRegex(
            PixelError, "Conflicting generic argument constraints"
        ):
            compile_project(
                {
                    "main.pxl": 'fn identity[T](value: T) -> T = value; fn main() { let x: int = identity("bad") }'
                }
            )
