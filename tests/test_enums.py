import copy
import json
import unittest

from pixellang.model import PixelError
from pixellang.compiler_client import CompilerClient
from pixellang.temporal import Timeline
from pixellang.vm import VM, validate_bytecode


class EnumTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def compile(self, code):
        response = self.compiler.request('compile', files={'main.pxl': code})
        self.assertEqual(response['status'], 'ok', response)
        return response['result']['bytecode']

    def test_generic_payload_image_recovery_and_every_replay_state(self):
        code = """enum Event[T] { Stop, Data(value: T) }
effect fn main() {
 let event: Event[[int]] = Event::[[int]].Data([3, 1])
 match event {
   Event.Data(values) => { var copy = values; copy[0] += 4; print(copy) }
   Event.Stop => { print([]: [int]) }
 }
 print(match event { Event.Stop => 0, Event.Data(values) => values[0] })
}"""
        compiled = self.compiler.debug_project({"main.pxl": code})
        expected = [[7, 1], 3]
        self.assertEqual(VM(compiled.bytecode).run(), expected)
        image = self.compiler.request('export-png', **compiled.source_project)['result']['image']
        code = self.compiler.request('compile-png', image=image)['result']['bytecode']
        self.assertEqual(VM(code).run(), expected)
        recovered = self.compiler.request('recover-png', image=image)['result']
        rebuilt = self.compiler.debug_project(**recovered)
        self.assertEqual(VM(rebuilt.bytecode).run(), expected)
        timeline = Timeline(rebuilt.source, compiled=rebuilt)
        states = [timeline.vm.state()]
        while not timeline.vm.halted:
            timeline.advance()
            states.append(timeline.vm.state())
        document = timeline.document()
        snapshot = document['source_project']
        replayed = self.compiler.debug_project(snapshot['files'], snapshot['entry'])
        restored = Timeline.restore(document, compiled=replayed)
        for z in reversed(range(len(states))):
            self.assertEqual(restored.seek(z), states[z])

    def test_subject_once_and_unselected_branch_has_no_effect(self):
        compiled = self.compile("""fn subject(count: inout [int]) -> Option[int] {
 count[0] += 1
 return Option.Some(42)
}
effect fn unwanted() -> int { print(999); return 0 }
effect fn main() {
 var count: [int] = [0]
 print(match subject(&count) { Option.None => unwanted(), Option.Some(value) => value })
 print(count[0])
}""")
        self.assertEqual(VM(compiled).run(), [42, 1])

    def test_option_context_lookup_and_statement_return(self):
        compiled = self.compile("""fn defaultValue(value: Option[int]) -> int {
 match value { Option.Some(number) => { return number } Option.None => { return 10 } }
}
effect fn main() {
 let absent: Option[int] = Option.None
 let values = map[string, int]{"found": 4}
 print(defaultValue(absent))
 print(defaultValue(values.get("found")))
 print(defaultValue(values.get("missing")))
 print(defaultValue(Option::[int].Some(8)))
}""")
        self.assertEqual(VM(compiled).run(), [10, 4, 10, 8])

    def test_optional_json_missing_null_present(self):
        compiled = (
            self.compile("""record Config { limit: Option[int], title: string }
effect fn main() {
 for text in ["{\\"title\\":\\"a\\"}", "{\\"title\\":\\"b\\",\\"limit\\":null}", "{\\"title\\":\\"c\\",\\"limit\\":3}"] {
  let config = jsonDecode::[Config](text)
  match config { .Ok(value) => { print(match value.limit { Option.None => 10, Option.Some(number) => number }) }, .Err(error) => { assert(false) } }
 }
}""")
        )
        self.assertEqual(VM(compiled).run(), [10, 10, 3])

    def test_invalid_patterns_payloads_and_scope(self):
        bodies = [
            'let value = Option.None',
            "let value = Option::[int].Some(true)",
            "let value = Option::[int].Some()",
            "let value = Option::[int].Missing",
            'print(match Option.Some(1) { Option.None => 0 })',
            'print(match Option.Some(1) { Option.Some(x) => x, Option.Some(y) => y, Option.None => 0 })',
            'print(match Option.Some(1) { _ => 0, Option.None => 1 })',
            'print(match Option.Some(1) { Option.Some(x) => x, Option.None => 0, _ => 2 })',
            'print(match Option.Some(1) { Option.Some(x, y) => x, Option.None => 0 })',
            'print(match Option.Some(1) { Option.Some(x) => x, Option.None => true })',
            'match Option.Some(1) { Option.Some(x) => { x = 2 } Option.None => {} }',
            'match Option.Some(1) { Option.Some(x) => {} Option.None => {} }; print(x)',
            'let value = Option.Some(1); print(value.value)',
            'let value = jsonDecode::[Option[Option[int]]]("null")',
        ]
        for body in bodies:
            with self.subTest(body=body), self.assertRaises(PixelError):
                self.compile("effect fn main() { " + body + " }")

    def test_tagged_json_roundtrip_and_optional_container(self):
        compiled = self.compile("""enum Event { Stop, Data(value: int) }
effect fn main() {
 let original = Event.Data(42)
 let text = jsonStringify(jsonFrom(original))
 print(text)
 let decoded = jsonDecode::[Event](text)
 match decoded { .Ok(event) => { print(match event { Event.Data(value) => value, Event.Stop => 0 }) }, .Err(error) => { assert(false) } }
 let optional = jsonDecode::[Option[[Option[int]]]]("[null, 3]")
 match optional { .Ok(value) => { print(jsonStringify(jsonFrom(value))) }, .Err(error) => { assert(false) } }
}""")
        result = VM(compiled).run()
        self.assertEqual(
            json.loads(result[0]), {"variant": "Data", "fields": {"value": 42}}
        )
        self.assertEqual(result[1], 42)
        self.assertEqual(json.loads(result[2]), [None, 3])

    def test_json_recovery_has_structured_error_paths(self):
        compiled = (
            self.compile("""record Config { limit: Option[int], title: string }
effect fn main() {
 match jsonDecode::[Config](input()) { .Ok(value) => { assert(false) }, .Err(error) => { print(error.code); print(error.path) } }
}""")
        )
        for payload, code, path in [
            ({}, "json.missing_field", "$.title"),
            ({"title": "a", "extra": 1}, "json.unknown_field", "$.extra"),
            ({"title": "a", "limit": True}, "json.type_mismatch", "$.limit"),
        ]:
            with self.subTest(payload=payload):
                self.assertEqual(
                    VM(compiled, input_text=json.dumps(payload)).run(),
                    [code, path],
                )

    def test_loop_control_exits_nested_result_arms(self):
        compiled = self.compile('''effect fn main() {
 for number in [1,2,3] {
  match Option.Some(number) {
   Option.Some(value) => {
    match parseInt(string(value)) {
     .Ok(parsed) => { if parsed==1 {continue}; if parsed==3 {break}; print(parsed) },
     .Err(error) => {assert(false)}
    }
   }, Option.None => {}
  }
 }
 match jsonDecode::[int]("true") {.Ok(value)=>{assert(false)},.Err(error)=>{print(error.code)}}
}''')
        self.assertEqual(VM(compiled).run(), [2, "json.type_mismatch"])

    def test_multifile_generic_enum_and_function(self):
        files = {
            "model.pxl": "export enum Box[T] { Empty, Value(item: T) }",
            "main.pxl": """import "model.pxl" as model
fn extract[T](value: model.Box[T], fallback: T) -> T = match value {
 model.Box.Empty => fallback, model.Box.Value(item) => item
}
effect fn main() { print(extract::[int](model.Box::[int].Value(42), 0)) }""",
        }
        compiled = self.compiler.request("compile", files=files)["result"]["bytecode"]
        self.assertEqual(VM(compiled).run(), [42])
        image = self.compiler.request('export-png', files=files)['result']['image']
        restored = self.compiler.request('compile-png', image=image)['result']['bytecode']
        self.assertEqual(VM(restored).run(), [42])

    def test_bytecode_rejects_invalid_schema_and_variant(self):
        compiled = self.compile('effect fn main() { print(Option.Some(42)) }')
        for records in (None, 1, [[]]):
            bc = copy.deepcopy(compiled)
            bc["records"] = records
            with self.subTest(records=records), self.assertRaises(PixelError):
                validate_bytecode(bc)
        bc = copy.deepcopy(compiled)
        bc["enums"]["Option<int>"]["Some"]["value"] = "bool"
        with self.assertRaises(PixelError):
            validate_bytecode(bc)


if __name__ == "__main__":
    unittest.main()
