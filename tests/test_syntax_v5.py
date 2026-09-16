import unittest

from pixellang.model import PixelError
from pixellang.project import compile_project
from pixellang.text import parse_text
from pixellang.vm import VM


class SyntaxV5Tests(unittest.TestCase):
    def test_uniform_function_and_nested_array_types(self):
        source = """fn first(rows: [[int]]) -> int = rows[0][0]
fn main() {
    let rows: [[int]] = [[42]]
    print(first(rows))
}"""
        _, compiled = compile_project({"main.pxl": source})
        self.assertEqual(VM(compiled.bytecode).run(), [42])

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
        ]
        for source in cases:
            with self.subTest(source=source), self.assertRaises(PixelError):
                parse_text(source)

    def test_symbolic_logic_short_circuits(self):
        _, compiled = compile_project(
            {
                "main.pxl": "fn main() { print(false && (1 / 0 == 0)); print(!false || (1 / 0 == 0)) }"
            }
        )
        self.assertEqual(VM(compiled.bytecode).run(), [False, True])

    def test_empty_unit_function_is_valid(self):
        _, compiled = compile_project({"main.pxl": "fn main() {}"})
        self.assertEqual(VM(compiled.bytecode).run(), [])

    def test_empty_catch_survives_pixels(self):
        _, compiled = compile_project(
            {"main.pxl": 'fn main() { try { fail("ignored") } catch error {} }'}
        )
        self.assertEqual(VM(compiled.bytecode).run(), [])

    def test_var_uses_same_annotation_grammar_as_let(self):
        tree = parse_text("var total: int = 0")
        self.assertEqual(tree.data["body"][0].data["type"], "int")
        with self.assertRaises(PixelError):
            parse_text("var total int = 0")


if __name__ == "__main__":
    unittest.main()
