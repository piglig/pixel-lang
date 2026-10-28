import unittest

from pixellang.native_editor import NativeSymbolIndex
from pixellang.model import PixelError
from pixellang.compiler_client import CompilerClient
from pixellang.temporal import Timeline
from pixellang.vm import VM


class ErrorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def compile(self, files):
        response = self.compiler.request('compile', files=files)
        self.assertEqual(response['status'], 'ok', response)
        return response['result']['bytecode']

    def test_error_can_cross_function_boundary_and_roundtrip(self):
        source = """fn describe(error: Error) -> string = error.kind + ": " + error.message
fn reject() -> Result[int, Error] = .Err(Error{kind:"user",code:"user.rejected",message:"bad row",path:"",operation:"reject",expected:"",actual:""})
effect fn main() {
 match reject() { .Ok(value)=>{print(value)}, .Err(error)=>{print(describe(error))} }
}"""
        compiled = self.compiler.debug_project({"main.pxl": source})
        self.assertEqual(VM(compiled.bytecode).run(), ["user: bad row"])
        image = self.compiler.request('export-png', files={'main.pxl': source})['result']['image']
        recovered = self.compiler.request('recover-png', image=image)['result']
        self.assertIn('Error', recovered['files'][recovered['entry']])
        rebuilt = self.compiler.debug_project(**recovered)
        self.assertEqual(VM(rebuilt.bytecode).run(), ['user: bad row'])
        timeline = Timeline(rebuilt.source, compiled=rebuilt, checkpoint_interval=4)
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
                self.compile(
                    {
                        "main.pxl": 'effect fn inspect(error: Error) { '
                        + body
                        + " }\nfn main(){}"
                    }
                )

    def test_fatal_assert_is_not_converted_to_result(self):
        bytecode = self.compile(
            {
                "main.pxl": 'fn operation()->Result[int, Error] { assert(false); return .Ok(42) }\neffect fn main() { match operation() { .Ok(value)=>{print(value)}, .Err(error)=>{print("caught")} } }'
            }
        )
        vm = VM(bytecode)
        with self.assertRaises(PixelError):
            vm.run()
        self.assertEqual(vm.output, [])

    def test_error_field_completion(self):
        source = 'effect fn inspect(error: Error) { print(error.) }\nfn main(){}'
        def analyze(files):
            response = self.compiler.request('editor-index', files=files)
            self.assertEqual(response['status'], 'ok', response)
            return NativeSymbolIndex(files, 'main.pxl', response['result']['index'])
        result = analyze({'main.pxl': source}).complete(
            'main.pxl', source.index('error.') + 6, analyze=analyze,
        )
        self.assertEqual(
            {item["label"] for item in result},
            {"kind", "code", "message", "path", "operation", "expected", "actual"},
        )
