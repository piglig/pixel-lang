import json
import unittest

from pixellang.model import PixelError
from pixellang.compiler_client import CompilerClient
from pixellang.temporal import Timeline
from pixellang.vm import VM


class JsonDecodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def compile(self, files):
        response = self.compiler.request('compile', files=files)
        self.assertEqual(response['status'], 'ok', response)
        return response['result']['bytecode']

    def test_nested_nominal_types_images_and_replay(self):
        files = {
            "main.pxl": 'import "types.pxl" as types\neffect fn RunTest()->Result[int,Error] { let value = (jsonDecode::[types.Config](input()))?; print(jsonStringify(jsonFrom(value))) \n return .Ok(0)\n}\neffect fn main(){match RunTest(){.Ok(done)=>{},.Err(error)=>{print(error)}}}\n',
            "types.pxl": "export record Row { value: int, ok: bool }\nexport record Config { title: string, rows: [Row], totals: map[string, int], metadata: json }",
        }
        payload = {
            "title": "猫",
            "rows": [{"value": 2**63 - 1, "ok": True}],
            "totals": {"a": 3},
            "metadata": None,
        }
        data = json.dumps(payload)
        compiled = self.compiler.debug_project(files)
        expected = VM(compiled.bytecode, input_text=data).run()
        self.assertEqual(json.loads(expected[0]), payload)
        image = self.compiler.request('export-png', files=files)['result']['image']
        restored = self.compiler.request('recover-png', image=image)['result']
        self.assertIn("jsonDecode::[", restored['files'][restored['entry']])
        rebuilt = self.compiler.debug_project(**restored)
        self.assertEqual(VM(rebuilt.bytecode, input_text=data).run(), expected)
        timeline = Timeline(rebuilt.source, compiled=rebuilt, input_text=data, checkpoint_interval=2)
        timeline.run()
        self.assertEqual(Timeline.restore(timeline.document()).vm.output, expected)

    def test_missing_extra_wrong_and_fractional_fields_are_recoverable(self):
        source = "record Row { value: int }\neffect fn main() { match jsonDecode::[[Row]](input()) { .Ok(value) => { print(value) }, .Err(error) => { print(error.kind); print(error.path); print(error.message) } } }"
        bytecode = self.compile({"main.pxl": source})
        for data, path, detail in [
            ("[{}]", "$[0]", "Missing field value"),
            ('[{"value":1,"extra":2}]', "$[0]", "Unknown field extra"),
            ('[{"value":"1"}]', "$[0].value", "Expected JSON number"),
            ('[{"value":1.5}]', "$[0].value", "exact signed 64-bit"),
            ('[{"value":9223372036854775808}]', "$[0].value", "exact signed 64-bit"),
            ("[null]", "$[0]", "Expected JSON object"),
        ]:
            with self.subTest(data=data):
                output = VM(bytecode, input_text=data).run()
                self.assertEqual(output[0], "json")
                self.assertIn(path, output[1])
                self.assertIn(detail, output[2])

    def test_gc_keeps_partial_decoded_objects_rooted(self):
        source = 'record Row { values: [int] }\neffect fn RunTest()->Result[int,Error] { let parsed = (jsonParse(input()))?; let rows = (jsonDecode::[[Row]](parsed))?; print(rows[0].values); print(rows[599].values) \n return .Ok(0)\n}\neffect fn main(){match RunTest(){.Ok(done)=>{},.Err(error)=>{print(error)}}}\n'
        bytecode = self.compile({"main.pxl": source})
        vm = VM(
            bytecode,
            input_text=json.dumps([{"values": [n]} for n in range(600)]),
        )
        self.assertEqual(vm.run(), [[0], [599]])
        self.assertGreater(vm.collections, 0)

    def test_empty_containers_scalar_and_exact_exponent(self):
        source = """effect fn show[T](result: Result[T,Error]) {
 match result { .Ok(value) => { print(value) }, .Err(error) => { print(error) } }
}
effect fn main() {
 show(jsonDecode::[[int]]("[]")); show(jsonDecode::[map[string,bool]]("{}"))
 show(jsonDecode::[int]("1.20e2")); show(jsonDecode::[bool]("false"))
}"""
        self.assertEqual(VM(self.compile({"main.pxl": source})).run(), [[], {}, 120, False])

    def test_decode_rejects_invalid_type_or_input_before_execution(self):
        for expression in (
            "jsonDecode::[Missing](input())",
            "jsonDecode::[int](42)",
            "jsonDecode::[fn()](input())",
            "jsonDecode::[input()](input())",
            "jsonDecode(input())",
        ):
            with self.subTest(expression=expression), self.assertRaises(PixelError):
                self.compile({"main.pxl": "effect fn main() { print(" + expression + ") }"})
