import unittest

from pixellang.editor import SymbolIndex
from pixellang.model import PixelError
from pixellang.project import compile_project
from pixellang.vm import VM


class ShorthandEditorV5Tests(unittest.TestCase):
    source = """record Row { amount: int }
fn main() {
 let amount = 42
 let row = Row{amount}
 print(row.amount)
}"""

    def rename(self, offset, new_name, source=None):
        source = source or self.source
        index = SymbolIndex({"main.pxl": source})
        edits = index.rename("main.pxl", offset, new_name)
        changed = source
        for edit in reversed(edits):
            span = edit["span"]
            changed = (
                changed[: span["start"]["offset"]]
                + edit["text"]
                + changed[span["end"]["offset"] :]
            )
        self.assertEqual(
            VM(compile_project({"main.pxl": changed})[1].bytecode).run(), [42]
        )
        return changed

    def test_rename_variable_expands_value_only(self):
        changed = self.rename(self.source.index("amount ="), "total")
        self.assertIn("Row{amount: total}", changed)
        self.assertIn("print(row.amount)", changed)

    def test_rename_field_expands_field_only(self):
        changed = self.rename(self.source.index("amount:"), "total")
        self.assertIn("Row{total: amount}", changed)
        self.assertIn("print(row.total)", changed)

    def test_shorthand_navigation_prefers_variable_both_references_preserved(self):
        index = SymbolIndex({"main.pxl": self.source})
        start = self.source.index("Row{amount}") + 4
        definition = index.definition("main.pxl", start)
        self.assertEqual(definition["start"]["offset"], self.source.index("amount ="))
        self.assertEqual(len(index.references("main.pxl", start)), 2)
        self.assertEqual(
            len(index.references("main.pxl", self.source.index("amount:"))), 3
        )
        self.assertIn("Row{amount: total}", self.rename(start, "total"))

    def test_unrelated_rename_preserves_both_identities(self):
        self.assertIn(
            "let item = Row{amount}", self.rename(self.source.index("row ="), "item")
        )

    def test_rename_rejects_shadow_capture_in_shorthand(self):
        source = self.source.replace("let row =", "let total = 7\n let row =")
        with self.assertRaises(PixelError):
            SymbolIndex({"main.pxl": source}).rename(
                "main.pxl", source.index("amount ="), "total"
            )
