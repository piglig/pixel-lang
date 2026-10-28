import unittest

from pixellang.model import PixelError
from pixellang.compiler_client import CompilerClient
from pixellang.vm import VM


class SyntaxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def run_source(self, text, extra=None):
        code = self.compiler.request('compile', files={'main.pxl': text, **(extra or {})})['result']['bytecode']
        return VM(code).run()

    def test_inferred_and_typed_bindings(self):
        self.assertEqual(
            self.run_source(
                "effect fn main() {\nlet a = [3, 1, 2]\nvar b: [int] = []\nb.append(a[0])\nprint(b)\n}"
            ),
            [[3]],
        )

    def test_expression_function(self):
        self.assertEqual(
            self.run_source("fn square(n: int) -> int = n * n\neffect fn main() {\nprint(square(7))\n}"),
            [49],
        )

    def test_contextual_identifier_names_and_anonymous_functions(self):
        source = '''record Box { value: int }
fn Read(component: Box) -> int = component.value
fn Apply(component: fn(int) -> int) -> int = component(3)
fn Check(component: fn(bool) -> bool) -> int {
 if component(true) { return 7 }
 return 0
}
effect fn main() {
 let increment = fn(x: int) -> int = x + 1
 print(Read(Box{value: 9}))
 print(Apply(fn(x: int) -> int = x * 2))
 print(Check(fn(x: bool) -> bool = x))
 print(increment(3))
}'''
        self.assertEqual(self.run_source(source), [9, 6, 7, 4])
        formatted = self.compiler.request('format', files={'main.pxl': source})['result']['text']
        self.assertEqual(self.run_source(formatted), [9, 6, 7, 4])
        image = self.compiler.request('export-png', files={'main.pxl': source})['result']['image']
        code = self.compiler.request('compile-png', image=image)['result']['bytecode']
        self.assertEqual(VM(code).run(), [9, 6, 7, 4])
        recovered = self.compiler.request('recover-png', image=image)['result']
        code = self.compiler.request('compile', **recovered)['result']['bytecode']
        self.assertEqual(VM(code).run(), [9, 6, 7, 4])

    def test_nested_types_and_block_function(self):
        self.assertEqual(
            self.run_source(
                "fn first(a: [[int]]) -> int { return a[0][0] }\neffect fn main() {\nlet a: [[int]] = [[42]]\nprint(first(a))\n}"
            ),
            [42],
        )

    def test_readable_import_alias(self):
        self.assertEqual(
            self.run_source(
                'import "math.pxl" as math\neffect fn main() {\nprint(math.square(7))\n}',
                {"math.pxl": "export fn square(n: int) -> int = n * n"},
            ),
            [49],
        )

    def test_main_and_mutation(self):
        self.assertEqual(
            self.run_source(
                "effect fn main() {\nvar count = 0\nwhile count < 3 { count += 1 }\nprint(count)\n}"
            ),
            [3],
        )

    def test_reject_untyped_expression_return(self):
        with self.assertRaises(PixelError):
            self.run_source("fn answer() = 42\neffect fn main() {}")

    def test_nested_type_limit(self):
        with self.assertRaisesRegex(PixelError, "Type nesting exceeds 128"):
            self.run_source("effect fn main(){let a: " + "[" * 129 + "int" + "]" * 129 + " = []}")

    def test_refined_formatter_roundtrip_precedence(self):
        text = "effect fn main() {\nlet x = 5\nprint(-(-x))\nprint(10 - (3 - 1))\nprint((2 + 3) * 4)\nlet a: [[int]] = [[], [2]]\nprint(a)\n}"
        recovered = self.compiler.request('format', files={'main.pxl': text})['result']['text']
        self.assertNotIn("var ", recovered)
        self.assertEqual(self.run_source(recovered), [5, 8, 20, [[], [2]]])
        self.assertEqual(self.compiler.request('format', files={'main.pxl': recovered})['result']['text'], recovered)

    def compile(self, files):
        return self.compiler.request('compile', files=files)['result']['bytecode']

    def test_uniform_function_and_nested_array_types(self):
        source = """fn first(rows: [[int]]) -> int = rows[0][0]
effect fn main() {
    let rows: [[int]] = [[42]]
    print(first(rows))
}"""
        compiled = self.compile({"main.pxl": source})
        self.assertEqual(VM(compiled).run(), [42])

    def test_removed_grammar_is_rejected(self):
        cases = [
            'import helper "helper.pxl"',
            "func f(n int) int { return n }",
            "fn f(n int) -> int { return n }",
            "fn f(n: int) int { return n }",
            "var values []int = []int{1}",
            "let values: []int = [1]",
            "let values = []int{1}",
            "let n = 1; n := 2",
            "let ok = true and false",
            "let ok = true or false",
            "let ok = not true",
            "for true { print(1) }",
            "while { print(1) }",
            "println(1)",
            'try { return } catch error {}',
            'let action = component() {}',
            'let values: mut [int] = [1]',
            'let values = freeze([1])',
        ]
        for source in cases:
            with self.subTest(source=source), self.assertRaises(PixelError):
                if source.startswith(('import ', 'func ', 'fn ')):
                    text = source + '\neffect fn main() {}'
                else:
                    text = 'effect fn main(){' + source + '}'
                self.compile({'main.pxl': text})

    def test_word_operator_names_are_ordinary_identifiers(self):
        code = self.compile({'main.pxl': 'effect fn main(){let and=true; let or=false; let not=3; print(and && !or); print(not)}'})
        self.assertEqual(VM(code).run(), [True, 3])

    def test_symbolic_logic_short_circuits(self):
        compiled = self.compile(
            {
                "main.pxl": "effect fn main() { print(false && (1 / 0 == 0)); print(!false || (1 / 0 == 0)) }"
            }
        )
        self.assertEqual(VM(compiled).run(), [False, True])

    def test_empty_unit_function_is_valid(self):
        compiled = self.compile({"main.pxl": "effect fn main() {}"})
        self.assertEqual(VM(compiled).run(), [])

    def test_empty_result_arms_survive_pixels(self):
        files = {"main.pxl": 'effect fn main() { match parseInt("invalid") { .Ok(value) => {}, .Err(error) => {} } }'}
        self.assertEqual(VM(self.compile(files)).run(), [])
        image = self.compiler.request('export-png', files=files)['result']['image']
        result = self.compiler.request('compile-png', image=image)['result']['bytecode']
        self.assertEqual(VM(result).run(), [])

    def test_var_uses_same_annotation_grammar_as_let(self):
        compiled = self.compile({'main.pxl': 'effect fn main(){var total: int = 0; total = 3; print(total)}'})
        self.assertEqual(VM(compiled).run(), [3])
        with self.assertRaises(PixelError):
            self.compile({'main.pxl': 'effect fn main(){var total int = 0}'})
