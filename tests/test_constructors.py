import unittest

from pixellang.model import PixelError
from pixellang.compiler_client import CompilerClient
from pixellang.vm import VM


class ConstructorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def request(self, operation, **args):
        response = self.compiler.request(operation, **args)
        self.assertEqual(response['status'], 'ok', response)
        return response['result']

    def run_source(self, source, extra=None):
        files = {"main.pxl": source, **(extra or {})}
        result = self.request('compile', files=files)
        pixels = self.request('pixels', files=files)['document']
        image = self.request('export-pixel-png', document=pixels)['image']
        recovered = self.request('recover-png', image=image)
        expected = VM(result['bytecode']).run()
        rebuilt = self.request('compile', files=recovered['files'], entry=recovered['entry'])
        self.assertEqual(VM(rebuilt['bytecode']).run(), expected)
        return expected

    def test_fields_shorthand_and_qualified_type(self):
        self.assertEqual(
            self.run_source(
                """import "models.pxl" as models
effect fn main() {
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
effect fn main() {
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
effect fn main() { let empty = Empty{}; let box = Box{row: Row{value: 7}}; print(box.row.value) }
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
                self.request(
                    "compile", files={
                        "main.pxl": "record Row { value: int }\neffect fn main() { "
                        + body
                        + " }"
                    }
                )


if __name__ == "__main__":
    unittest.main()
