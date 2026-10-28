import unittest

from tests.editor_support import NativeEditorCase
from pixellang.model import PixelError


class LanguageServiceTests(NativeEditorCase):
    def test_hover_tracks_changed_source_snapshot(self):
        from pixellang.workstation import Workstation
        service = Workstation()
        try:
            for value, expected in [('1', 'int'), ('"猫"', 'string'), ('true', 'bool')]:
                text = 'effect fn main() { let value = ' + value + '; print(value) }'
                args = {"files": {"main.pxl": text}, "source": "main.pxl",
                        "offset": text.rindex("value")}
                self.assertEqual(service.request("hover", args)["label"], "value: " + expected)
                self.assertEqual(service.request("document-symbols", args)[0]["name"], "main")
                self.assertEqual(service.request("hover", args)["label"], "value: " + expected)
        finally:
            service.close()

    def test_signature_help_ignores_nested_commas_and_string_delimiters(self):
        code = '''fn use(items: [int], text: string) {}
effect fn main() { use([1, 2], "(,)", '''
        result = self.index({"main.pxl": code}).signature_help("main.pxl", len(code))
        self.assertEqual(result["parameters"], ["items: [int]", "text: string"])
        self.assertEqual(result["activeParameter"], 1)

    def test_signature_help_for_imported_generic_call(self):
        code = 'import "lib.pxl" as lib\neffect fn main() { lib.choose::[int](1, '
        files = {"main.pxl": code, "lib.pxl":
                 "export fn choose[T](first: T, second: T) -> T = first"}
        result = self.index(files).signature_help("main.pxl", len(code))
        self.assertEqual(result["label"], "fn choose[T](first: T, second: T) -> T")
        self.assertEqual(result["activeParameter"], 1)

    def test_hover_resolves_generic_function_and_inferred_nominal_type(self):
        code = """record Row { value: int }
fn identity[T](value: T) -> T = value
effect fn main() {
 let row = identity(Row{value: 1})
 print(row.value)
}"""
        index = self.index({"main.pxl": code})
        self.assertEqual(index.hover("main.pxl", code.index("row.value"))["label"], "row: Row")
        self.assertEqual(index.hover("main.pxl", code.index("identity(Row"))["label"],
                         "fn identity[T](value: T) -> T")
        self.assertIsNone(index.hover("main.pxl", code.index("print")))

    def test_hover_and_outline_survive_unfinished_code(self):
        from pixellang.workstation import Workstation
        code = 'import "lib.pxl" as lib\neffect fn main() { print(lib.twice(2))\nlet unfinished ='
        args = {"files": {"main.pxl": code,
                          "lib.pxl": "export fn twice(n: int) -> int = n * 2"},
                "source": "main.pxl", "offset": code.index("twice")}
        service = Workstation()
        try:
            self.assertEqual(service.request("hover", args)["label"], "fn twice(n: int) -> int")
            symbols = service.request("document-symbols", args)
            self.assertEqual([s["name"] for s in symbols], ["main"])
            self.assertEqual(symbols[0]["selection"]["start"]["offset"], code.index("main"))
        finally:
            service.close()

    def test_nominal_mismatch_diagnostic_uses_source_type_names(self):
        files = {
            "models.pxl": "export record Row { value: int }",
            "main.pxl": """import "models.pxl" as models
record Other { value: int }
fn consume(value: models.Row) {}
effect fn main() { consume(Other{value: 1}) }""",
        }
        with self.assertRaisesRegex(PixelError, "Expected models.Row, got Other"):
            self.compile(files)

    def test_inferred_result_completion_and_binding_rename(self):
        code = """record Row { value: int }
fn identity[T](value: T) -> T = value
effect fn main() {
 let row = identity(Row{value: 1})
 print(row.value)
 row.
}"""
        result = self.complete(
            {"main.pxl": code}, "main.pxl", code.index("row.\n") + 4
        )
        self.assertTrue(
            any(c["label"] == "value" and c["detail"] == "int" for c in result)
        )
        valid = code.replace(" row.\n", "")
        index = self.index({"main.pxl": valid})
        references = index.references("main.pxl", valid.index("row ="))
        self.assertEqual(len(references), 2)
        renamed = index.rename("main.pxl", valid.index("row ="), "entryRow", analyze=self.index)
        self.assertEqual(len(renamed), 2)
