import unittest

from pixellang.model import PixelError
from pixellang.project import compile_project
from pixellang.vm import VM


class ModuleV5Tests(unittest.TestCase):
    def test_uppercase_function_is_private_without_export(self):
        files = {
            "main.pxl": 'import "lib.pxl"\nfn main() { print(lib.Value()) }',
            "lib.pxl": "fn Value() -> int = 42",
        }
        with self.assertRaisesRegex(PixelError, "private"):
            compile_project(files)
        files["lib.pxl"] = "export fn Value() -> int = 42"
        self.assertEqual(VM(compile_project(files)[1].bytecode).run(), [42])

    def test_uppercase_record_is_private_without_export(self):
        files = {
            "main.pxl": 'import "lib.pxl"\nfn main() { let item = lib.Item{value: 42}; print(item.value) }',
            "lib.pxl": "record Item { value: int }",
        }
        with self.assertRaisesRegex(PixelError, "private"):
            compile_project(files)
        files["lib.pxl"] = "export record Item { value: int }"
        self.assertEqual(VM(compile_project(files)[1].bytecode).run(), [42])

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
                compile_project({"main.pxl": code})
        with self.assertRaises(PixelError):
            compile_project(
                {"main.pxl": 'import "lib.pxl"\nfn main() {}', "lib.pxl": "print(1)"}
            )

    def test_entry_roundtrip_without_injected_statement(self):
        from pixellang.compiler import compile_source
        from pixellang.picture import decode_picture, encode_picture
        from pixellang.project import project_text

        files = {
            "main.pxl": 'import "lib.pxl"\nfn main() { print(42) }',
            "lib.pxl": "fn main() { print(99) }",
        }
        source, compiled = compile_project(files)
        self.assertEqual(len(compiled.bytecode["entry"]), 1)
        self.assertFalse(
            any(name.endswith(":init") for name in compiled.bytecode["functions"])
        )
        image_source = decode_picture(encode_picture(source))
        self.assertEqual(VM(compile_source(image_source).bytecode).run(), [42])
        recovered = project_text(image_source)
        from pixellang.text import parse_text

        tree = parse_text(recovered["files"]["main.pxl"])
        self.assertTrue(
            all(n.kind in ("fn", "record", "import") for n in tree.data["body"])
        )
        self.assertEqual(
            VM(compile_project(recovered["files"])[1].bytecode).run(), [42]
        )

    def test_lowercase_exports_work(self):
        files = {
            "main.pxl": 'import "lib.pxl"\nfn main() { print(lib.value()) }',
            "lib.pxl": "export fn value() -> int = 7",
        }
        self.assertEqual(VM(compile_project(files)[1].bytecode).run(), [7])


if __name__ == "__main__":
    unittest.main()
