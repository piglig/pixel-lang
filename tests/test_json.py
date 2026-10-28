import json
import unittest

from pixellang.compiler_client import CompilerClient
from pixellang.model import PixelError
from pixellang.temporal import Timeline
from pixellang.vm import VM


class JsonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = CompilerClient()
        cls.addClassCleanup(cls.client.close)

    def execute(self, code, input_text=""):
        compiled = self.client.request('compile', files={'main.pxl': code})['result']['bytecode']
        return VM(compiled, input_text=input_text).run()

    @staticmethod
    def program(body, declarations="", imports=""):
        return (imports + "\n" + declarations + "\neffect fn Process() -> Result[bool, Error] {\n"
                + body + "\nreturn .Ok(true)\n}\neffect fn main() {\n"
                + "match Process() { .Ok(value) => {}, .Err(error) => { print(error.kind) } }\n}")

    def test_exact_numbers_and_all_value_variants(self):
        output = self.execute(
            self.program('let root = json.Parse(input())?\nprint(json.Int(json.Get(root, "large")?)?)\nprint(json.Kind(json.At(json.Get(root, "values")?, 0)?))\nprint(json.Bool(json.At(json.Get(root, "values")?, 1)?)?)\nprint(json.String(json.At(json.Get(root, "values")?, 2)?)?)\nprint(json.Stringify(root))\nprint(len(root))\nprint(json.Keys(root)?)', imports='import "std/json.pxl" as json'),
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
                        self.program("print(jsonParse(input())?)"),
                        data,
                    ),
                    ["json"],
                )
        for number in ["1.5", "9223372036854775808", "-9223372036854775809", "1e400"]:
            self.assertEqual(
                self.execute(
                    self.program("print(jsonAsInt(jsonParse(input())?)?)"),
                    number,
                ),
                ["json"],
            )
        self.assertEqual(
            self.execute(self.program('print(jsonAsInt(jsonParse("1.20e2")?)?)')), [120]
        )
        self.assertEqual(
            self.execute(
                self.program('print(jsonAsBool(jsonParse("1")?)?)')
            ),
            ["json"],
        )

    def test_records_convert_to_immutable_json_snapshot(self):
        result = self.execute(
            'record Report { title: string, values: [int], enabled: bool }\neffect fn main() {\nvar report: Report = Report{title: "猫", values: [1, 2], enabled: true}\nlet snapshot = jsonFrom(report)\nreport.values[0] = 99\nprint(jsonStringify(snapshot))\nlet envelope = map[string, json]{"report": snapshot, "nothing": jsonNull()}\nprint(jsonStringify(jsonFrom(envelope)))\n}'
        )
        expected = {"title": "猫", "values": [1, 2], "enabled": True}
        self.assertEqual(json.loads(result[0]), expected)
        self.assertEqual(json.loads(result[1]), {"report": expected, "nothing": None})

    def test_recursive_value_copy_and_nesting_limits(self):
        result = self.execute(self.program(
            'var node = Node{children: []}; node.children.append(node); print(jsonStringify(jsonFrom(node)))',
            declarations='record Node { children: [Node] }'))
        self.assertEqual(json.loads(result[0]), {"children": [{"children": []}]})
        nested = "[" * 150 + "0" + "]" * 150
        self.assertEqual(
            self.execute(
                self.program("print(jsonParse(input())?)"),
                nested,
            ),
            ["json"],
        )

    def test_image_and_timeline_roundtrip(self):
        code = self.program('let value = json.Parse(input())?; var i = 0; while i < 30 { print(json.Int(json.At(value, i % 3)?)?); i += 1 }', imports='import "std/json.pxl" as json')
        files = {'main.pxl': code}
        image = self.client.request('export-png', files=files)['result']['image']
        expected = [1, 2, 3] * 10
        compiled = self.client.request('compile-png', image=image)['result']['bytecode']
        self.assertEqual(VM(compiled, input_text='[1,2,3]').run(), expected)
        recovered = self.client.request('recover-png', image=image)['result']
        rebuilt = self.client.request('compile', **recovered)['result']['bytecode']
        self.assertEqual(VM(rebuilt, input_text='[1,2,3]').run(), expected)
        artifact = self.client.debug_project(files)
        timeline = Timeline(artifact.source, compiled=artifact, input_text='[1,2,3]', checkpoint_interval=16)
        timeline.run()
        for z in [40, 15, 42]:
            reference = timeline.fresh_vm()
            for _ in range(z):
                reference.step(snapshot=False)
            self.assertEqual(timeline.seek(z), reference.state())
        self.assertEqual(
            Timeline.restore(timeline.document(), compiled=artifact).vm.state(), timeline.vm.state()
        )

    def test_json_type_is_distinct_from_string(self):
        with self.assertRaises(PixelError):
            self.execute('effect fn main() {\nlet value: json = "{}"\n}')
        self.assertEqual(
            self.execute(
                self.program('let value: json = jsonParse("{}")?; print(jsonKind(value))')
            ),
            ["object"],
        )

    def test_debug_snapshot_does_not_expand_large_immutable_json(self):
        compiled = self.client.request('compile', files={
            'main.pxl': self.program('let value = jsonParse(input())?; print(len(value))')
        })['result']['bytecode']
        vm = VM(compiled, input_text=json.dumps(list(range(10000))))
        for _ in range(200):
            vm.step(snapshot=False)
            state = vm.state()
            if any(v == {"type": "json", "kind": "array", "length": 10000}
                   for v in state["memory"]):
                break
        self.assertLess(len(json.dumps(state)), 5000)
        self.assertTrue(
            any(
                v == {"type": "json", "kind": "array", "length": 10000}
                for v in state["memory"]
            )
        )
