import unittest

from tests.editor_support import NativeEditorCase
from pixellang.model import PixelError
from pixellang.vm import VM


class ShorthandEditorTests(NativeEditorCase):
    source = """record Row { amount: int }
effect fn main() {
 let amount = 42
 let row = Row{amount}
 print(row.amount)
}"""

    def rename(self, offset, new_name, source=None):
        source = source or self.source
        index = self.index({"main.pxl": source})
        edits = index.rename("main.pxl", offset, new_name, analyze=self.index)
        changed = source
        for edit in reversed(edits):
            span = edit["span"]
            changed = (
                changed[: span["start"]["offset"]]
                + edit["text"]
                + changed[span["end"]["offset"] :]
            )
        self.assertEqual(
            VM(self.compile({"main.pxl": changed})).run(), [42]
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
        index = self.index({"main.pxl": self.source})
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
            self.index({"main.pxl": source}).rename(
                "main.pxl", source.index("amount ="), "total"
            , analyze=self.index)
