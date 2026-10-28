from tests.native_compiler import native_service
import unittest
from pathlib import Path

from pixellang.native_editor import NativeSymbolIndex

from pixellang.model import PixelError
from pixellang.vm import VM


class ConciseCallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = native_service()
        cls.addClassCleanup(cls.service.close)
        stdlib = Path(__file__).resolve().parents[1] / 'pixellang/stdlib'
        cls.libraries = {'std/' + p.name: p.read_text() for p in stdlib.glob('*.pxl')}

    def native(self, operation, **args):
        if 'files' in args:
            args['files'] = {**self.libraries, **args['files']}
        response = self.service.request(dict(version=1, operation=operation, **args))
        self.assertEqual(response['status'], 'ok', response)
        return response['result']

    def compile(self, files):
        response = self.service.request(dict(version=1, operation='compile', files=files))
        if response['status'] != 'ok':
            self.assertTrue(response.get('diagnostics'), response)
            diagnostic = response['diagnostics'][0]
            raise PixelError(diagnostic['phase'], diagnostic['message'])
        return response['result']['bytecode']

    def native_index(self, files):
        files = {**self.libraries, **files}
        result = self.native('editor-index', files=files)['index']
        return NativeSymbolIndex(files, 'main.pxl', result)

    def test_expression_callbacks_capture_and_compose_across_modules(self):
        files = {
            "main.pxl": """import "std/collections.pxl" as collections
effect fn main() {
 let minimum = 2
 let chosen = collections.Filter([3, 1, 2], fn(value) = value >= minimum)
 let sorted = collections.SortedBy(chosen, fn(a, b) = a < b)
 print(collections.Map(sorted, fn(value) = string(value)))
 let make = fn(offset: int) -> fn(int) -> int = fn(value: int) -> int = value + offset
 print(make(10)(5))
}"""
        }
        expected = [["2", "3"], 15]
        self.assertEqual(VM(self.native('compile', files=files)['bytecode']).run(), expected)
        image = self.native('export-png', files=files)['image']
        self.assertEqual(VM(self.native('compile-png', image=image)['bytecode']).run(), expected)
        recovered = self.native('recover-png', image=image)
        self.assertEqual(VM(self.native('compile', **recovered)['bytecode']).run(), expected)
        formatted = self.native('format', files=files)['text']
        self.assertEqual(self.native('format', files={'main.pxl': formatted})['text'], formatted)
        self.assertEqual(VM(self.native('compile', files={'main.pxl': formatted})['bytecode']).run(), expected)

    def test_expression_callback_return_type_is_checked(self):
        with self.assertRaisesRegex(PixelError, "Type mismatch"):
            self.compile(
                {
                    "main.pxl": "effect fn main() { let f = fn(x: int) -> int = x > 0; print(f(1)) }"
                }
            )

    def test_contextual_blocks_and_dependency_order(self):
        code = """import "std/collections.pxl" as collections
fn apply[T, U](f: fn(T) -> U, value: T) -> U = f(value)
fn choose[T](empty: [T], value: T) -> T = value
effect fn main() {
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
            VM(self.native('compile', files={"main.pxl": code})['bytecode']).run(),
            [5, "7", 9, ["small", "big"], 42],
        )

    def test_ambiguity_and_inconsistent_inferred_returns(self):
        cases = [
            ("effect fn main() { let f = fn(x) = x }", "Cannot infer parameter type"),
            (
                "effect fn main() { let f = fn(x: bool) { if x { return 1 }; return false } }",
                "Type mismatch",
            ),
            (
                "effect fn main() { let f = fn(x: bool) { if x { return 1 } } }",
                "Not every path returns a value",
            ),
            ("effect fn main() { let f: fn(int) -> int = fn(x, y) = x }", "Cannot infer parameter type"),
        ]
        for code, error in cases:
            with self.subTest(code=code), self.assertRaisesRegex(PixelError, error):
                self.compile({"main.pxl": code})

    def test_contextual_parameter_completion_and_rename(self):
        code = """import "std/collections.pxl" as collections
record Row { amount: int }
effect fn main() {
 let rows = [Row{amount: 1}]
 let values = collections.Map(rows, fn(row) = row.amount)
 print(values)
}
"""
        index = self.native_index({"main.pxl": code})
        self.assertEqual(
            len(index.rename("main.pxl", code.index("fn(row)") + len("fn("),
                             "item", analyze=self.native_index)), 2
        )
        incomplete = code.replace("row.amount", "row.")
        completions = self.native_index({"main.pxl": incomplete}).complete(
            "main.pxl", incomplete.index("row.") + 4, analyze=self.native_index,
        )
        self.assertTrue(
            any(c["label"] == "amount" and c["detail"] == "int" for c in completions),
            completions,
        )

    def test_expected_results_constrain_empty_arguments(self):
        code = """fn identity[T](value: T) -> T = value
fn empty[T]() -> [T] { return [] }
effect fn main() {
 let values: [int] = identity([])
 let nested: [[string]] = identity([[]])
 let absent: Option[int] = identity(Option.None)
 let more: [int] = empty::[int]()
 print(len(values)); print(nested); print(len(more))
 print(match absent { Option.None => true, Option.Some(value) => false })
}
"""
        files = {"main.pxl": code}
        compiled = self.compile(files)
        expected = [0, [[]], 0, True]
        self.assertEqual(VM(compiled).run(), expected)
        image = self.native('export-png', files=files)['image']
        self.assertEqual(VM(self.native('compile-png', image=image)['bytecode']).run(), expected)
        with self.assertRaisesRegex(
            PixelError, "Conflicting type parameter constraints"
        ):
            self.compile(
                {
                    "main.pxl": 'fn identity[T](value: T) -> T = value; effect fn main() { let x: int = identity("bad") }'
                }
            )
