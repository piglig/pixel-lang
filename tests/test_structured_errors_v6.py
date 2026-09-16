import tempfile
import unittest
from pathlib import Path

from pixellang.fileaccess import FileAccess
from pixellang.project import compile_project
from pixellang.temporal import Timeline
from pixellang.vm import VM


class StructuredErrorTests(unittest.TestCase):
    def test_json_errors_have_stable_code_and_structured_context(self):
        source = """record Row { amount: int }
fn main() {
 try { print(jsonDecode[[Row]](input())) }
 catch e { print(e.kind); print(e.code); print(e.path); print(e.operation); print(e.expected); print(e.actual) }
}"""
        _, compiled = compile_project({"main.pxl": source})
        for text, code, path, expected, actual in [
            ("[{}]", "json.missing_field", "$[0].amount", "int", "missing"),
            (
                '[{"amount":"bad"}]',
                "json.type_mismatch",
                "$[0].amount",
                "number",
                "string",
            ),
            ('[{"amount":2,"extra":1}]', "json.unknown_field", "$[0].extra", "", ""),
            ("[", "json.invalid_syntax", "$", "", ""),
            ('[{"amount":1.5}]', "json.numeric_range", "$[0].amount", "int", "1.5"),
        ]:
            with self.subTest(text=text):
                self.assertEqual(
                    VM(compiled.bytecode, input_text=text).run(),
                    ["json", code, path, "jsonDecode", expected, actual],
                )

    def test_json_map_paths_escape_keys(self):
        _, compiled = compile_project(
            {
                "main.pxl": """fn main() {
 try { print(jsonDecode[map[int]](input())) }
 catch e { print(e.path); print(e.code) }
}"""
            }
        )
        self.assertEqual(
            VM(compiled.bytecode, input_text='{"a.b":[1]}').run(),
            ['$["a.b"]', "json.type_mismatch"],
        )

    def test_collection_numeric_and_user_errors_are_distinguishable(self):
        _, compiled = compile_project(
            {
                "main.pxl": """fn main() {
 try { print(map[int]{}["absent"]) } catch e { print(e.code); print(e.path) }
 try { print(1.0 / 0.0) } catch e { print(e.code) }
 try { print(parseInt("bad")) } catch e { print(e.code); print(e.operation) }
 try { fail("invalid row") } catch e { print(e.code); print(e.message) }
}"""
            }
        )
        self.assertEqual(
            VM(compiled.bytecode).run(),
            [
                "collection.missing_key",
                "absent",
                "numeric.division_by_zero",
                "numeric.invalid_text",
                "parseInt",
                "user.failure",
                "invalid row",
            ],
        )

    def test_file_codes_survive_replay_after_external_state_changes(self):
        source, compiled = compile_project(
            {
                "main.pxl": """fn main() {
 try { print(readText("missing.txt")) }
 catch e { print(e.kind); print(e.code); print(e.path); print(e.operation) }
}"""
            }
        )
        self.assertEqual(
            VM(compiled.bytecode).run(),
            ["io", "io.access_denied", "missing.txt", "read"],
        )
        with tempfile.TemporaryDirectory() as directory:
            timeline = Timeline(source, file_access=FileAccess(read_root=directory))
            expected = ["io", "io.not_found", "missing.txt", "read"]
            self.assertEqual(timeline.run()["output"], expected)
            document = timeline.document()
            Path(directory, "missing.txt").write_text("now exists")
            restored = Timeline.restore(document)
            restored.seek(0)
            self.assertEqual(restored.seek(len(restored.events))["output"], expected)

    def test_file_decode_and_path_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "binary").write_bytes(b"\xff")
            access = FileAccess(read_root=directory)
            _, compiled = compile_project(
                {
                    "main.pxl": """fn main() {
 try { readText("binary") } catch e { print(e.code) }
 try { readText("../outside") } catch e { print(e.code) }
}"""
                }
            )
            self.assertEqual(
                VM(compiled.bytecode, file_access=access).run(),
                ["io.invalid_encoding", "io.invalid_path"],
            )


if __name__ == "__main__":
    unittest.main()
