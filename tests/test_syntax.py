import unittest

from pixellang.model import PixelError
from pixellang.project import compile_project
from pixellang.vm import VM


def run(text, extra=None):
    _, compiled = compile_project({"main.pxl": text, **(extra or {})})
    return VM(compiled.bytecode).run()


class RefinedSyntaxTests(unittest.TestCase):
    def test_inferred_and_typed_bindings(self):
        self.assertEqual(
            run(
                "fn main() {\nlet a = [3, 1, 2]\nlet b: [int] = []\nappend(b, a[0])\nprint(b)\n}"
            ),
            [[3]],
        )

    def test_expression_function(self):
        self.assertEqual(
            run("fn square(n: int) -> int = n * n\nfn main() {\nprint(square(7))\n}"),
            [49],
        )

    def test_nested_types_and_block_function(self):
        self.assertEqual(
            run(
                "fn first(a: [[int]]) -> int { return a[0][0] }\nfn main() {\nlet a: [[int]] = [[42]]\nprint(first(a))\n}"
            ),
            [42],
        )

    def test_readable_import_alias(self):
        self.assertEqual(
            run(
                'import "math.pxl" as math\nfn main() {\nprint(math.square(7))\n}',
                {"math.pxl": "export fn square(n: int) -> int = n * n"},
            ),
            [49],
        )

    def test_main_and_mutation(self):
        self.assertEqual(
            run(
                "fn main() {\nvar count = 0\nwhile count < 3 { count += 1 }\nprint(count)\n}"
            ),
            [3],
        )

    def test_reject_untyped_expression_return(self):
        with self.assertRaises(PixelError):
            run("fn answer() = 42")

    def test_nested_type_limit(self):
        with self.assertRaises(PixelError):
            run("let a: " + "[" * 40 + "int" + "]" * 40 + " = []")

    def test_refined_formatter_roundtrip_precedence(self):
        from pixellang.project import project_text

        text = "fn main() {\nlet x = 5\nprint(-(-x))\nprint(10 - (3 - 1))\nprint((2 + 3) * 4)\nlet a: [[int]] = [[], [2]]\nprint(a)\n}"
        source, _ = compile_project({"main.pxl": text})
        recovered = project_text(source)["files"]["main.pxl"]
        self.assertNotIn("var ", recovered)
        self.assertEqual(run(recovered), [5, 8, 20, [[], [2]]])
