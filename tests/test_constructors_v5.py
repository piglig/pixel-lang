import unittest

from pixellang.model import PixelError
from pixellang.picture import decode_picture, encode_picture
from pixellang.project import compile_project, project_text
from pixellang.vm import VM


class ConstructorV5Tests(unittest.TestCase):
    def run_source(self, source, extra=None):
        files = {"main.pxl": source, **(extra or {})}
        image, result = compile_project(files)
        recovered = project_text(decode_picture(encode_picture(image)))
        expected = VM(result.bytecode).run()
        self.assertEqual(
            VM(compile_project(recovered["files"])[1].bytecode).run(), expected
        )
        return expected

    def test_fields_shorthand_and_qualified_type(self):
        self.assertEqual(
            self.run_source(
                """import "models.pxl" as models
fn main() {
 let category = "books"
 let amount = 42
 let item = models.Sale{category, amount}
 print(item.category)
 print(item.amount)
}""",
                {"models.pxl": "export record Sale { category: string, amount: int }"},
            ),
            ["books", 42],
        )

    def test_control_block_and_constructor_are_unambiguous(self):
        self.assertEqual(
            self.run_source("""record Flag { value: bool }
fn main() {
 var ready = true
 while ready { ready = false }
 if !ready && (Flag{value: true}).value { print(42) }
}"""),
            [42],
        )

    def test_empty_constructor_and_nested_fields(self):
        self.assertEqual(
            self.run_source("""record Empty {}
record Row { value: int }
record Box { row: Row }
fn main() { let empty = Empty{}; let box = Box{row: Row{value: 7}}; print(box.row.value) }
"""),
            [7],
        )

    def test_shorthand_requires_binding_and_exact_fields(self):
        for body in (
            "let item = Row{value}",
            "let value = 1; let item = Row{value, value}",
            'let value = "bad"; let item = Row{value}',
            "let item = new Row{value: 1}",
        ):
            with self.subTest(body=body), self.assertRaises(PixelError):
                compile_project(
                    {
                        "main.pxl": "record Row { value: int }\nfn main() { "
                        + body
                        + " }"
                    }
                )


if __name__ == "__main__":
    unittest.main()
