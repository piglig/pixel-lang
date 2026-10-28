import unittest

from pixellang.model import PixelError
from pixellang.compiler_client import CompilerClient
from pixellang.vm import VM


class ModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def request(self, operation, **args):
        response = self.compiler.request(operation, **args)
        self.assertEqual(response['status'], 'ok', response)
        return response['result']

    def compile(self, files):
        return self.request('compile', files=files)['bytecode']

    def test_uppercase_function_is_private_without_export(self):
        files = {
            "main.pxl": 'import "lib.pxl"\neffect fn main() { print(lib.Value()) }',
            "lib.pxl": "fn Value() -> int = 42",
        }
        with self.assertRaisesRegex(PixelError, "private"):
            self.compile(files)
        files["lib.pxl"] = "export fn Value() -> int = 42"
        self.assertEqual(VM(self.compile(files)).run(), [42])

    def test_uppercase_record_is_private_without_export(self):
        files = {
            "main.pxl": 'import "lib.pxl"\neffect fn main() { let item = lib.Item{value: 42}; print(item.value) }',
            "lib.pxl": "record Item { value: int }",
        }
        with self.assertRaisesRegex(PixelError, "private"):
            self.compile(files)
        files["lib.pxl"] = "export record Item { value: int }"
        self.assertEqual(VM(self.compile(files)).run(), [42])

    def test_main_is_required_and_top_level_execution_rejected(self):
        cases = [
            "fn helper() {}",
            "print(42)",
            "fn main() {}\nprint(42)",
            "fn main(x: int) {}",
            "fn main() -> int = 1",
        ]
        for code in cases:
            with self.subTest(code=code), self.assertRaises(PixelError):
                self.compile({"main.pxl": code})
        with self.assertRaises(PixelError):
            self.compile(
                {"main.pxl": 'import "lib.pxl"\nfn main() {}', "lib.pxl": "print(1)"}
            )

    def test_entry_roundtrip_without_injected_statement(self):
        files = {
            "main.pxl": 'import "lib.pxl"\neffect fn main() { print(42) }',
            "lib.pxl": "effect fn main() { print(99) }",
        }
        compiled = self.compile(files)
        self.assertEqual(len(compiled["entry"]), 1)
        self.assertFalse(any(name.endswith(":init") for name in compiled["functions"]))
        image = self.request('export-png', files=files)['image']
        self.assertEqual(VM(self.request('compile-png', image=image)['bytecode']).run(), [42])
        recovered = self.request('recover-png', image=image)
        self.assertIn(recovered['entry'], recovered['files'])
        self.assertEqual(VM(self.request('compile', **recovered)['bytecode']).run(), [42])

    def test_lowercase_exports_work(self):
        files = {
            "main.pxl": 'import "lib.pxl"\neffect fn main() { print(lib.value()) }',
            "lib.pxl": "export fn value() -> int = 7",
        }
        self.assertEqual(VM(self.compile(files)).run(), [7])


if __name__ == "__main__":
    unittest.main()
