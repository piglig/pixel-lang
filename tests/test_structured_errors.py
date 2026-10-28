import tempfile
import unittest
from pathlib import Path

from pixellang.compiler_client import CompilerClient
from pixellang.fileaccess import FileAccess
from pixellang.temporal import Timeline
from pixellang.vm import VM
from pixellang.model import PixelError


class StructuredErrorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def compile(self, files):
        response = self.compiler.request('compile', files=files)
        self.assertEqual(response['status'], 'ok', response)
        return response['result']['bytecode']

    def test_json_errors_have_stable_code_and_structured_context(self):
        source = """record Row { amount: int }
effect fn main() {
 match jsonDecode::[[Row]](input()) { .Ok(value)=>{print(value)}, .Err(e)=>{print(e.kind); print(e.code); print(e.path); print(e.operation); print(e.expected); print(e.actual)} }
}"""
        bytecode = self.compile({"main.pxl": source})
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
                    VM(bytecode, input_text=text).run(),
                    ["json", code, path, "jsonDecode", expected, actual],
                )

    def test_json_map_paths_escape_keys(self):
        bytecode = self.compile(
            {
                "main.pxl": """effect fn main() {
 match jsonDecode::[map[string, int]](input()) { .Ok(value)=>{print(value)}, .Err(e)=>{print(e.path); print(e.code)} }
}"""
            }
        )
        self.assertEqual(
            VM(bytecode, input_text='{"a.b":[1]}').run(),
            ['$["a.b"]', "json.type_mismatch"],
        )

    def test_collection_numeric_and_user_errors_are_distinguishable(self):
        for expression, code in [('map[string,int]{}["absent"]','collection.missing_key'),
                                 ('1.0 / 0.0','numeric.division_by_zero')]:
            with self.subTest(expression=expression):
                bytecode=self.compile({'main.pxl':'effect fn main(){print('+expression+')} '})
                with self.assertRaises(PixelError) as error:
                    VM(bytecode).run()
                self.assertEqual(error.exception.code,code)
        bytecode=self.compile({'main.pxl':'''fn validate()->Result[int,Error] = .Err(Error{
 kind:"user",code:"user.failure",message:"invalid row",path:"",operation:"validate",expected:"",actual:""})
effect fn main(){
 match parseInt("bad") {.Ok(value)=>{print(value)},.Err(e)=>{print(e.code);print(e.operation)}}
 match validate() {.Ok(value)=>{print(value)},.Err(e)=>{print(e.code);print(e.message)}}
}'''})
        self.assertEqual(VM(bytecode).run(),['numeric.invalid_text','parseInt','user.failure','invalid row'])

    def test_file_codes_survive_replay_after_external_state_changes(self):
        compiled = self.compiler.debug_project(
            {
                "main.pxl": """effect fn main() {
 match readText("missing.txt") { .Ok(value)=>{print(value)}, .Err(e)=>{print(e.kind); print(e.code); print(e.path); print(e.operation)} }
}"""
            }
        )
        self.assertEqual(
            VM(compiled.bytecode).run(),
            ["io", "io.access_denied", "missing.txt", "read"],
        )
        with tempfile.TemporaryDirectory() as directory:
            timeline = Timeline(compiled.source, compiled=compiled, file_access=FileAccess(read_root=directory))
            expected = ["io", "io.not_found", "missing.txt", "read"]
            self.assertEqual(timeline.run()["output"], expected)
            document = timeline.document()
            Path(directory, "missing.txt").write_text("now exists")
            restored = Timeline.restore(document, compiled=compiled)
            restored.seek(0)
            self.assertEqual(restored.seek(len(restored.events))["output"], expected)

    def test_file_decode_and_path_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "binary").write_bytes(b"\xff")
            access = FileAccess(read_root=directory)
            bytecode = self.compile(
                {
                    "main.pxl": """effect fn main() {
 match readText("binary") {.Ok(value)=>{print(value)},.Err(e)=>{print(e.code)}}
 match readText("../outside") {.Ok(value)=>{print(value)},.Err(e)=>{print(e.code)}}
}"""
                }
            )
            self.assertEqual(
                VM(bytecode, file_access=access).run(),
                ["io.invalid_encoding", "io.invalid_path"],
            )


if __name__ == "__main__":
    unittest.main()
