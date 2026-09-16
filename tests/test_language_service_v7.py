import unittest

from pixellang.editor import SymbolIndex, complete
from pixellang.model import PixelError
from pixellang.project import compile_project


class LanguageServiceTests(unittest.TestCase):
    def test_nominal_mismatch_diagnostic_uses_source_type_names(self):
        files = {
            "models.pxl": "export record Row { value: int }",
            "main.pxl": """import "models.pxl" as models
record Other { value: int }
fn consume(value: models.Row) {}
fn main() { consume(Other{value: 1}) }""",
        }
        with self.assertRaisesRegex(PixelError, "Expected models.Row, got Other"):
            compile_project(files)

    def test_inferred_result_completion_and_binding_rename(self):
        code = """record Row { value: int }
fn identity[T](value: T) -> T = value
fn main() {
 let row = identity(Row{value: 1})
 print(row.value)
 row.
}"""
        result = complete(
            {"main.pxl": code}, "main.pxl", "main.pxl", code.index("row.\n") + 4
        )
        self.assertTrue(
            any(c["label"] == "value" and c["detail"] == "int" for c in result)
        )
        valid = code.replace(" row.\n", "")
        index = SymbolIndex({"main.pxl": valid})
        references = index.references("main.pxl", valid.index("row ="))
        self.assertEqual(len(references), 2)
        renamed = index.rename("main.pxl", valid.index("row ="), "entryRow")
        self.assertEqual(len(renamed), 2)
