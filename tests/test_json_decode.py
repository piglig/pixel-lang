import json
import unittest

from pixellang.model import PixelError
from pixellang.picture import decode_picture, encode_picture
from pixellang.project import compile_project, project_text
from pixellang.temporal import Timeline
from pixellang.vm import VM


class JsonDecodeTests(unittest.TestCase):
    def test_nested_nominal_types_images_and_replay(self):
        files = {
            "main.pxl": 'import "types.pxl" as types\nfn main() { let value = jsonDecode[types.Config](input()); print(jsonStringify(jsonFrom(value))) }',
            "types.pxl": "export record Row { value: int, ok: bool }\nexport record Config { title: string, rows: [Row], totals: map[int], metadata: json }",
        }
        payload = {
            "title": "猫",
            "rows": [{"value": 2**63 - 1, "ok": True}],
            "totals": {"a": 3},
            "metadata": None,
        }
        data = json.dumps(payload)
        source, compiled = compile_project(files)
        expected = VM(compiled.bytecode, input_text=data).run()
        self.assertEqual(json.loads(expected[0]), payload)
        restored = project_text(decode_picture(encode_picture(source)))
        self.assertIn("jsonDecode[types.Config]", restored["files"]["main.pxl"])
        self.assertEqual(
            VM(compile_project(restored["files"])[1].bytecode, input_text=data).run(),
            expected,
        )
        timeline = Timeline(source, input_text=data, checkpoint_interval=2)
        timeline.run()
        self.assertEqual(Timeline.restore(timeline.document()).vm.output, expected)

    def test_missing_extra_wrong_and_fractional_fields_are_recoverable(self):
        source = "record Row { value: int }\nfn main() { try { print(jsonDecode[[Row]](input())) } catch error { print(error.kind); print(error.message) } }"
        _, compiled = compile_project({"main.pxl": source})
        for data, path, detail in [
            ("[{}]", "$[0]", "Missing field value"),
            ('[{"value":1,"extra":2}]', "$[0]", "Unknown field extra"),
            ('[{"value":"1"}]', "$[0].value", "Expected JSON number"),
            ('[{"value":1.5}]', "$[0].value", "exact signed 64-bit"),
            ('[{"value":9223372036854775808}]', "$[0].value", "exact signed 64-bit"),
            ("[null]", "$[0]", "Expected JSON object"),
        ]:
            with self.subTest(data=data):
                output = VM(compiled.bytecode, input_text=data).run()
                self.assertEqual(output[0], "json")
                self.assertIn(path, output[1])
                self.assertIn(detail, output[1])

    def test_gc_keeps_partial_decoded_objects_rooted(self):
        source = "record Row { values: [int] }\nfn main() { let rows = jsonDecode[[Row]](jsonParse(input())); print(rows[0].values); print(rows[599].values) }"
        _, compiled = compile_project({"main.pxl": source})
        vm = VM(
            compiled.bytecode,
            input_text=json.dumps([{"values": [n]} for n in range(600)]),
        )
        self.assertEqual(vm.run(), [[0], [599]])
        self.assertGreater(vm.collections, 0)

    def test_empty_containers_scalar_and_exact_exponent(self):
        _, compiled = compile_project(
            {
                "main.pxl": 'fn main() { print(jsonDecode[[int]]("[]")); print(jsonDecode[map[bool]]("{}")); print(jsonDecode[int]("1.20e2")); print(jsonDecode[bool]("false")) }'
            }
        )
        self.assertEqual(VM(compiled.bytecode).run(), [[], {}, 120, False])

    def test_decode_rejects_invalid_type_or_input_before_execution(self):
        for expression in (
            "jsonDecode[Missing](input())",
            "jsonDecode[int](42)",
            "jsonDecode[Error](input())",
            "jsonDecode[input()](input())",
            "jsonDecode(input())",
        ):
            with self.subTest(expression=expression), self.assertRaises(PixelError):
                compile_project({"main.pxl": "fn main() { print(" + expression + ") }"})
