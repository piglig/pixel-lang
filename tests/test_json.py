import json
import unittest

from pixellang.compiler import compile_source
from pixellang.model import PixelError
from pixellang.picture import decode_picture, encode_picture
from pixellang.project import compile_project, project_text
from pixellang.temporal import Timeline
from pixellang.vm import VM


class JsonTests(unittest.TestCase):
    def execute(self, code, input_text=""):
        _, compiled = compile_project({"main.pxl": code})
        return VM(compiled.bytecode, input_text=input_text).run()

    def test_exact_numbers_and_all_value_variants(self):
        output = self.execute(
            'import "std/json.pxl" as json\nfn main() {\nlet root = json.Parse(input())\nprint(json.Int(json.Get(root, "large")))\nprint(json.Kind(json.At(json.Get(root, "values"), 0)))\nprint(json.Bool(json.At(json.Get(root, "values"), 1)))\nprint(json.String(json.At(json.Get(root, "values"), 2)))\nprint(json.Stringify(root))\nprint(len(root))\nprint(json.Keys(root))\n}',
            '{"large":9223372036854775807,"values":[null,true,"猫🌍"],"decimal":1.2345678901234567890123456789,"exponent":1e400}',
        )
        self.assertEqual(output[:4], [2**63 - 1, "null", True, "猫🌍"])
        self.assertIn("1.2345678901234567890123456789", output[4])
        self.assertIn("1e400", output[4])
        self.assertEqual(output[5:], [4, ["large", "values", "decimal", "exponent"]])

    def test_parse_failures_and_checked_conversions_are_recoverable(self):
        for data in ['{"x":1,"x":2}', "[NaN]", "{", '"\\ud800"', "[1,]", "Infinity"]:
            with self.subTest(data=data):
                self.assertEqual(
                    self.execute(
                        "fn main() {\ntry { print(jsonParse(input())) } catch error { print(error.kind) }\n}",
                        data,
                    ),
                    ["json"],
                )
        for number in ["1.5", "9223372036854775808", "-9223372036854775809", "1e400"]:
            self.assertEqual(
                self.execute(
                    "fn main() {\ntry { print(jsonAsInt(jsonParse(input()))) } catch error { print(error.kind) }\n}",
                    number,
                ),
                ["json"],
            )
        self.assertEqual(
            self.execute('fn main() {\nprint(jsonAsInt(jsonParse("1.20e2")))\n}'), [120]
        )
        self.assertEqual(
            self.execute(
                'fn main() {\ntry { print(jsonAsBool(jsonParse("1"))) } catch error { print(error.kind) }\n}'
            ),
            ["json"],
        )

    def test_records_convert_to_immutable_json_snapshot(self):
        result = self.execute(
            'record Report { title: string, values: [int], enabled: bool }\nfn main() {\nlet report = Report{title: "猫", values: [1, 2], enabled: true}\nlet snapshot = jsonFrom(report)\nreport.values[0] = 99\nprint(jsonStringify(snapshot))\nlet envelope = map[json]{"report": snapshot, "nothing": jsonNull()}\nprint(jsonStringify(jsonFrom(envelope)))\n}'
        )
        expected = {"title": "猫", "values": [1, 2], "enabled": True}
        self.assertEqual(json.loads(result[0]), expected)
        self.assertEqual(json.loads(result[1]), {"report": expected, "nothing": None})

    def test_cyclic_record_conversion_and_nesting_limits(self):
        self.assertEqual(
            self.execute(
                "record Node { children: [Node] }\nfn main() {\nlet node = Node{children: []}\nappend(node.children, node)\ntry { print(jsonFrom(node)) } catch error { print(error.kind) }\n}"
            ),
            ["json"],
        )
        nested = "[" * 150 + "0" + "]" * 150
        self.assertEqual(
            self.execute(
                "fn main() {\ntry { print(jsonParse(input())) } catch error { print(error.kind) }\n}",
                nested,
            ),
            ["json"],
        )

    def test_image_and_timeline_roundtrip(self):
        code = 'import "std/json.pxl" as json\nfn main() {\nlet value = json.Parse(input())\nvar i = 0\nwhile i < 30 { print(json.Int(json.At(value, i % 3))); i += 1 }\n}'
        source, _ = compile_project({"main.pxl": code})
        image = decode_picture(encode_picture(source))
        expected = [1, 2, 3] * 10
        self.assertEqual(
            VM(compile_source(image).bytecode, input_text="[1,2,3]").run(), expected
        )
        recovered = project_text(image)
        self.assertEqual(
            VM(
                compile_project(recovered["files"])[1].bytecode, input_text="[1,2,3]"
            ).run(),
            expected,
        )
        timeline = Timeline(source, input_text="[1,2,3]", checkpoint_interval=16)
        timeline.run()
        for z in [40, 15, 42]:
            reference = timeline.fresh_vm()
            for _ in range(z):
                reference.step(snapshot=False)
            self.assertEqual(timeline.seek(z), reference.state())
        self.assertEqual(
            Timeline.restore(timeline.document()).vm.state(), timeline.vm.state()
        )

    def test_json_type_is_distinct_from_string(self):
        with self.assertRaises(PixelError):
            self.execute('fn main() {\nlet value: json = "{}"\n}')
        self.assertEqual(
            self.execute(
                'fn main() {\nlet value: json = jsonParse("{}")\nprint(jsonKind(value))\n}'
            ),
            ["object"],
        )

    def test_debug_snapshot_does_not_expand_large_immutable_json(self):
        _, compiled = compile_project(
            {
                "main.pxl": "fn main() {\nlet value = jsonParse(input())\nprint(len(value))\n}"
            }
        )
        vm = VM(compiled.bytecode, input_text=json.dumps(list(range(10000))))
        for _ in range(3):
            vm.step(snapshot=False)
        state = vm.state()
        self.assertLess(len(json.dumps(state)), 5000)
        self.assertTrue(
            any(
                v == {"type": "json", "kind": "array", "length": 10000}
                for v in state["memory"]
            )
        )
