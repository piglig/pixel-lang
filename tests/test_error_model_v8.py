import tempfile
import unittest

from pixellang.fileaccess import FileAccess
from pixellang.model import PixelError
from pixellang.project import compile_project
from pixellang.temporal import Timeline
from pixellang.vm import VM


class ErrorModelTests(unittest.TestCase):
    def test_absence_is_distinct_from_invalid_data_and_required_key(self):
        code = """import "std/maps.pxl" as maps
record Config { limit: Option[int] }
fn main() {
 let config = jsonDecode[Config]("{}")
 print(match config.limit { None => "absent", Some(n) => "present" })
 print(match maps.Get(map[int]{}, "missing") { None => "absent", Some(n) => "present" })
 try { jsonDecode[Config]("{\\"limit\\":\\"bad\\"}") } catch error { print(error.code); print(error.path) }
 try { let value = map[int]{}["missing"] } catch error { print(error.code) }
}"""
        self.assertEqual(
            VM(compile_project({"main.pxl": code})[1].bytecode).run(),
            [
                "absent",
                "absent",
                "json.type_mismatch",
                "$.limit",
                "collection.missing_key",
            ],
        )

    def test_application_result_preserves_structured_error_as_data(self):
        code = """enum Outcome[T] { Ok(value: T), Failed(error: Error) }
fn parse(value: string) -> Outcome[int] {
 try { return Outcome[int].Ok(parseInt(value)) }
 catch error { return Outcome[int].Failed(error) }
}
fn main() {
 for value in ["42", "invalid"] {
  match parse(value) {
   Outcome.Ok(number) => { print(number) }
   Outcome.Failed(error) => { print(error.code); print(error.operation) }
  }
 }
}"""
        self.assertEqual(
            VM(compile_project({"main.pxl": code})[1].bytecode).run(),
            [42, "numeric.invalid_text", "parseInt"],
        )

    def test_fatal_faults_cannot_be_converted_to_recoverable_errors(self):
        for operation in ["assert(false)", "let n = 9223372036854775807; print(n + 1)"]:
            code = (
                "fn main() { try { "
                + operation
                + ' } catch error { print("caught") } }'
            )
            with self.subTest(operation=operation), self.assertRaises(PixelError):
                VM(compile_project({"main.pxl": code})[1].bytecode).run()

    def test_recorded_file_failure_is_stable_when_file_appears(self):
        from pathlib import Path

        code = """fn main() {
 try { print(readText("missing.txt")) } catch error {
  print(error.code); print(error.path); print(error.operation)
 }
}"""
        source, _ = compile_project({"main.pxl": code})
        with tempfile.TemporaryDirectory() as directory:
            timeline = Timeline(source, file_access=FileAccess(directory, directory))
            timeline.run()
            expected = list(timeline.vm.output)
            self.assertEqual(expected[0], "io.not_found")
            self.assertEqual(expected[1], "missing.txt")
            Path(directory, "missing.txt").write_text("now present")
            restored = Timeline.restore(timeline.document())
            restored.seek(0)
            self.assertEqual(restored.seek(len(restored.events))["output"], expected)
