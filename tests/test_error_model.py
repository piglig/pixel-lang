import tempfile
import unittest

from pixellang.compiler_client import CompilerClient
from pixellang.fileaccess import FileAccess
from pixellang.model import PixelError
from pixellang.temporal import Timeline
from pixellang.vm import VM


class ErrorModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def compile(self, files):
        response = self.compiler.request('compile', files=files)
        self.assertEqual(response['status'], 'ok', response)
        return response['result']['bytecode']

    def test_absence_is_distinct_from_invalid_data_and_required_key(self):
        code = """import "std/collections.pxl" as collections
record Config { limit: Option[int] }
effect fn RunTest()->Result[int,Error] {
 let config = (jsonDecode::[Config]("{}"))?
 print(match config.limit { Option.None => "absent", Option.Some(n) => "present" })
 print(match map[string, int]{}.get("missing") { Option.None => "absent", Option.Some(n) => "present" })
 match jsonDecode::[Config]("{\\"limit\\":\\"bad\\"}") { .Ok(value) => { print(value) }, .Err(error) => { print(error.code); print(error.path) } }
 let value = map[string, int]{}["missing"]
 print("unreachable")

 return .Ok(0)
}
effect fn main(){match RunTest(){.Ok(done)=>{},.Err(error)=>{print(error)}}}
"""
        vm = VM(self.compile({"main.pxl": code}))
        with self.assertRaises(PixelError) as raised:
            vm.run()
        self.assertEqual(raised.exception.code, "collection.missing_key")
        self.assertEqual(
            vm.output,
            [
                "absent",
                "absent",
                "json.type_mismatch",
                "$.limit",
            ],
        )

    def test_application_result_preserves_structured_error_as_data(self):
        code = """enum Outcome[T] { Ok(value: T), Failed(error: Error) }
fn parse(value: string) -> Outcome[int] {
 return match parseInt(value) { .Ok(number) => Outcome::[int].Ok(number), .Err(error) => Outcome::[int].Failed(error) }
}
effect fn main() {
 for value in ["42", "invalid"] {
  match parse(value) {
   Outcome.Ok(number) => { print(number) }
   Outcome.Failed(error) => { print(error.code); print(error.operation) }
  }
 }
}"""
        self.assertEqual(
            VM(self.compile({"main.pxl": code})).run(),
            [42, "numeric.invalid_text", "parseInt"],
        )

    def test_fatal_faults_cannot_be_converted_to_recoverable_errors(self):
        for operation in ["assert(false)", "let n = 9223372036854775807; print(n + 1)"]:
            code = "effect fn main() { " + operation + '; print("unreachable") }'
            compiled = self.compile({"main.pxl": code})
            vm = VM(compiled)
            with self.subTest(operation=operation), self.assertRaises(PixelError):
                vm.run()
            self.assertEqual(vm.output, [])

    def test_recorded_file_failure_is_stable_when_file_appears(self):
        from pathlib import Path

        code = """effect fn main() {
 match readText("missing.txt") {
  .Ok(value) => { print(value) },
  .Err(error) => { print(error.code); print(error.path); print(error.operation) }
 }
}"""
        compiled = self.compiler.debug_project({"main.pxl": code})
        with tempfile.TemporaryDirectory() as directory:
            timeline = Timeline(compiled.source, compiled=compiled, file_access=FileAccess(directory, directory))
            timeline.run()
            expected = list(timeline.vm.output)
            self.assertEqual(expected[0], "io.not_found")
            self.assertEqual(expected[1], "missing.txt")
            Path(directory, "missing.txt").write_text("now present")
            restored = Timeline.restore(timeline.document())
            restored.seek(0)
            self.assertEqual(restored.seek(len(restored.events))["output"], expected)
