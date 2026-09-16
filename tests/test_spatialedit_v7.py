import unittest

from pixellang.model import PixelError
from pixellang.project import compile_project
from pixellang.spatialedit import SpatialProject
from pixellang.vm import VM


class SpatialEditTests(unittest.TestCase):
    def setUp(self):
        self.files = {
            "main.pxl": 'import "math.pxl" as math\nfn main() { print(math.Value()) }',
            "math.pxl": "export fn Value() -> int {\n let x = 2 + 3\n print(10)\n print(20)\n return x\n}",
        }
        self.project = SpatialProject(self.files)

    def target(self, kind, text):
        return next(
            t
            for t in self.project.describe("math.pxl")["targets"].values()
            if t["kind"] == kind and t["text"].strip() == text
        )

    def edit(self, target, action, value=None):
        return self.project.edit(
            {
                "revision": self.project.revision,
                "target": target["id"],
                "action": action,
                "value": value,
            }
        )

    def test_pixel_mapping_and_literal_edit(self):
        target = self.target("literal", "2")
        self.assertEqual(target["explanation"], "Produces a constant int value.")
        view = self.project.describe("math.pxl")
        self.assertTrue(
            any(
                target["id"] in token["targets"]
                for r in view["regions"]
                for token in r["tokens"]
            )
        )
        result = self.edit(target, "literal", "7")
        self.assertEqual(
            VM(compile_project(result["files"])[1].bytecode).run(), [10, 20, 10]
        )
        self.assertEqual(result["files"]["main.pxl"], self.files["main.pxl"])
        self.assertEqual(self.project.files, self.files)

    def test_operator_edit_preserves_grouping(self):
        result = self.edit(self.target("binary", "2 + 3"), "operator", "*")
        self.assertEqual(
            VM(compile_project(result["files"])[1].bytecode).run(), [10, 20, 6]
        )

    def test_operator_preserves_comments_and_child_expression_grouping(self):
        project = SpatialProject(
            {"main.pxl": "fn main() { print((1 + 2) /* keep */ * (3 + 4)) }"}
        )
        target = next(
            t
            for t in project.describe("main.pxl")["targets"].values()
            if t["kind"] == "binary" and "keep" in t["text"]
        )
        result = project.edit(
            {
                "revision": project.revision,
                "target": target["id"],
                "action": "operator",
                "value": "-",
            }
        )
        self.assertIn("/* keep */", result["files"]["main.pxl"])
        self.assertEqual(VM(compile_project(result["files"])[1].bytecode).run(), [-4])

    def test_move_statement_and_reject_broken_dependency(self):
        result = self.edit(self.target("print", "print(20)"), "up")
        self.assertEqual(
            VM(compile_project(result["files"])[1].bytecode).run(), [20, 10, 5]
        )
        with self.assertRaises(PixelError):
            self.edit(self.target("return", "return x"), "up")

    def test_invalid_literal_type_and_stale_snapshot_leave_source_intact(self):
        target = self.target("literal", "2")
        for value in [
            '"bad"',
            "1 + 2",
            "1); print(2",
            "1) } fn extra() {} fn other() { print(2",
        ]:
            with self.subTest(value=value), self.assertRaises(PixelError):
                self.edit(target, "literal", value)
        with self.assertRaisesRegex(PixelError, "Source changed"):
            self.project.edit(
                {
                    "revision": "stale",
                    "target": target["id"],
                    "action": "literal",
                    "value": "7",
                }
            )
        self.assertEqual(self.project.files, self.files)
