import unittest

from pixellang.editor import complete
from pixellang.model import PixelError
from pixellang.picture import decode_picture, encode_picture
from pixellang.project import compile_project, project_text
from pixellang.temporal import Timeline
from pixellang.vm import VM


class ErrorV5Tests(unittest.TestCase):
    def test_error_can_cross_function_boundary_and_roundtrip(self):
        source = """fn describe(error: Error) -> string = error.kind + ": " + error.message
fn main() {
 try { fail("bad row") } catch error { print(describe(error)) }
}"""
        image, compiled = compile_project({"main.pxl": source})
        self.assertEqual(VM(compiled.bytecode).run(), ["user: bad row"])
        recovered = project_text(decode_picture(encode_picture(image)))
        self.assertIn("Error", recovered["files"]["main.pxl"])
        self.assertEqual(
            VM(compile_project(recovered["files"])[1].bytecode).run(), ["user: bad row"]
        )
        timeline = Timeline(image, checkpoint_interval=4)
        timeline.run()
        document = timeline.document()
        self.assertEqual(Timeline.restore(document).vm.output, ["user: bad row"])
        for position in range(1, len(timeline.events) + 1):
            timeline.seek(position)
        self.assertEqual(timeline.vm.output, ["user: bad row"])

    def test_fields_checked_and_readonly(self):
        for body in (
            "print(error.mesage)",
            'print(error["message"])',
            'error.message = "changed"',
        ):
            with self.subTest(body=body), self.assertRaises(PixelError):
                compile_project(
                    {
                        "main.pxl": 'fn main() { try { fail("bad") } catch error { '
                        + body
                        + " } }"
                    }
                )

    def test_fatal_assert_is_not_caught(self):
        _, compiled = compile_project(
            {
                "main.pxl": 'fn main() { try { assert(false) } catch error { print("caught") } }'
            }
        )
        vm = VM(compiled.bytecode)
        with self.assertRaises(PixelError):
            vm.run()
        self.assertEqual(vm.output, [])

    def test_error_field_completion(self):
        source = 'fn main() { try { fail("bad") } catch error { print(error.) } }'
        result = complete(
            {"main.pxl": source}, "main.pxl", "main.pxl", source.index("error.") + 6
        )
        self.assertEqual(
            {item["label"] for item in result},
            {"kind", "code", "message", "path", "operation", "expected", "actual"},
        )
