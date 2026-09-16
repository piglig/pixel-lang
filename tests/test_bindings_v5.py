import unittest

from pixellang.compiler import compile_source
from pixellang.model import PixelError
from pixellang.picture import decode_picture, encode_picture
from pixellang.project import compile_project, project_text
from pixellang.vm import VM


class BindingV5Tests(unittest.TestCase):
    def test_var_survives_pixel_image_and_text_recovery(self):
        code = "fn main() { var total: int = 0; for value in [1, 2, 3] { total += value }; print(total) }"
        source, compiled = compile_project({"main.pxl": code})
        self.assertEqual(VM(compiled.bytecode).run(), [6])
        decoded = decode_picture(encode_picture(source))
        self.assertEqual(VM(compile_source(decoded).bytecode).run(), [6])
        recovered = project_text(decoded)
        self.assertIn("var total", recovered["files"]["main.pxl"])
        self.assertEqual(VM(compile_project(recovered["files"])[1].bytecode).run(), [6])

    def test_let_parameter_and_iteration_rebinding_rejected(self):
        cases = [
            "fn main() { let n = 1; n = 2 }",
            "fn main() { let n = 1; n += 2 }",
            "fn change(n: int) { n = 2 }\nfn main() { change(1) }",
            "fn main() { for n in [1, 2] { n = 3 } }",
            'fn main() { try { fail("x") } catch error { error = map[string]{} } }',
        ]
        for source in cases:
            with (
                self.subTest(source=source),
                self.assertRaisesRegex(PixelError, "immutable"),
            ):
                compile_project({"main.pxl": source})

    def test_let_does_not_freeze_objects(self):
        code = """record Box { value: int }
fn main() {
 let values = [1]
 append(values, 2)
 values[0] = 3
 let totals = map[int]{"x": 1}
 totals["x"] = 4
 let box = Box{value: 1}
 box.value = 5
 print(values)
 print(totals["x"])
 print(box.value)
}"""
        self.assertEqual(
            VM(compile_project({"main.pxl": code})[1].bytecode).run(), [[3, 2], 4, 5]
        )

    def test_shadowing_uses_nearest_binding_mutability(self):
        good = (
            "fn main() { let n = 1; if true { var n = 2; n += 1; print(n) }; print(n) }"
        )
        self.assertEqual(
            VM(compile_project({"main.pxl": good})[1].bytecode).run(), [3, 1]
        )
        bad = "fn main() { var n = 1; if true { let n = 2; n += 1 } }"
        with self.assertRaisesRegex(PixelError, "immutable"):
            compile_project({"main.pxl": bad})


if __name__ == "__main__":
    unittest.main()
