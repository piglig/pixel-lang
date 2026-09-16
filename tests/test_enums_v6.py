import copy
import json
import unittest

from pixellang.compiler import compile_source
from pixellang.model import PixelError
from pixellang.picture import decode_picture, encode_picture
from pixellang.project import compile_project, project_text
from pixellang.temporal import Timeline
from pixellang.vm import VM, validate_bytecode


class EnumTests(unittest.TestCase):
    def compile(self, code):
        return compile_project({"main.pxl": code})

    def test_generic_payload_image_recovery_and_every_replay_state(self):
        source, compiled = self.compile("""enum Event[T] { Stop, Data(value: T) }
fn main() {
 let event = Event[[int]].Data([3, 1])
 match event {
   Event.Data(values) => { values[0] += 4; print(values) }
   Event.Stop => { print([]: [int]) }
 }
 print(match event { Event.Stop => 0, Event.Data(values) => values[0] })
}""")
        expected = [[7, 1], 7]
        self.assertEqual(VM(compiled.bytecode).run(), expected)
        decoded = decode_picture(encode_picture(source))
        self.assertEqual(VM(compile_source(decoded).bytecode).run(), expected)
        recovered = project_text(decoded)["files"]
        self.assertEqual(VM(compile_project(recovered)[1].bytecode).run(), expected)
        timeline = Timeline(decoded)
        states = [timeline.vm.state()]
        while not timeline.vm.halted:
            timeline.advance()
            states.append(timeline.vm.state())
        restored = Timeline.restore(timeline.document())
        for z in reversed(range(len(states))):
            self.assertEqual(restored.seek(z), states[z])

    def test_subject_once_and_unselected_branch_has_no_effect(self):
        _, compiled = self.compile("""fn subject(count: [int]) -> Option[int] {
 count[0] += 1
 return Some(42)
}
fn unwanted() -> int { print(999); return 0 }
fn main() {
 let count = [0]
 print(match subject(count) { None => unwanted(), Some(value) => value })
 print(count[0])
}""")
        self.assertEqual(VM(compiled.bytecode).run(), [42, 1])

    def test_option_context_lookup_and_statement_return(self):
        _, compiled = self.compile("""fn defaultValue(value: Option[int]) -> int {
 match value { Some(number) => { return number } None => { return 10 } }
}
fn main() {
 let absent: Option[int] = None
 let values = map[int]{"found": 4}
 print(defaultValue(absent))
 print(defaultValue(lookup(values, "found")))
 print(defaultValue(lookup(values, "missing")))
 print(defaultValue(Option[int].Some(8)))
}""")
        self.assertEqual(VM(compiled.bytecode).run(), [10, 4, 10, 8])

    def test_optional_json_missing_null_present(self):
        _, compiled = (
            self.compile(r"""record Config { limit: Option[int], title: string }
fn main() {
 for text in ["{\"title\":\"a\"}", "{\"title\":\"b\",\"limit\":null}", "{\"title\":\"c\",\"limit\":3}"] {
  let config = jsonDecode[Config](text)
  print(match config.limit { None => 10, Some(value) => value })
 }
}""")
        )
        self.assertEqual(VM(compiled.bytecode).run(), [10, 10, 3])

    def test_invalid_patterns_payloads_and_scope(self):
        bodies = [
            "let value = None",
            "let value = Option[int].Some(true)",
            "let value = Option[int].Some()",
            "let value = Option[int].Missing",
            "print(match Some(1) { None => 0 })",
            "print(match Some(1) { Some(x) => x, Some(y) => y, None => 0 })",
            "print(match Some(1) { _ => 0, None => 1 })",
            "print(match Some(1) { Some(x) => x, None => 0, _ => 2 })",
            "print(match Some(1) { Some(x, y) => x, None => 0 })",
            "print(match Some(1) { Some(x) => x, None => true })",
            "match Some(1) { Some(x) => { x = 2 } None => {} }",
            "match Some(1) { Some(x) => {} None => {} }; print(x)",
            "let value = Some(1); print(value.value)",
            'let value = jsonDecode[Option[Option[int]]]("null")',
        ]
        for body in bodies:
            with self.subTest(body=body), self.assertRaises(PixelError):
                self.compile("fn main() { " + body + " }")

    def test_tagged_json_roundtrip_and_optional_container(self):
        _, compiled = self.compile("""enum Event { Stop, Data(value: int) }
fn main() {
 let original = Event.Data(42)
 let text = jsonStringify(jsonFrom(original))
 print(text)
 let decoded = jsonDecode[Event](text)
 print(match decoded { Event.Data(value) => value, Event.Stop => 0 })
 let optional = jsonDecode[Option[[Option[int]]]]("[null, 3]")
 print(jsonStringify(jsonFrom(optional)))
}""")
        result = VM(compiled.bytecode).run()
        self.assertEqual(
            json.loads(result[0]), {"variant": "Data", "fields": {"value": 42}}
        )
        self.assertEqual(result[1], 42)
        self.assertEqual(json.loads(result[2]), [None, 3])

    def test_json_recovery_has_structured_error_paths(self):
        _, compiled = (
            self.compile("""record Config { limit: Option[int], title: string }
fn main() {
 try { print(jsonDecode[Config](input())) }
 catch error { print(error.code); print(error.path) }
}""")
        )
        for payload, code, path in [
            ({}, "json.missing_field", "$.title"),
            ({"title": "a", "extra": 1}, "json.unknown_field", "$.extra"),
            ({"title": "a", "limit": True}, "json.type_mismatch", "$.limit"),
        ]:
            with self.subTest(payload=payload):
                self.assertEqual(
                    VM(compiled.bytecode, input_text=json.dumps(payload)).run(),
                    [code, path],
                )

    def test_loop_control_unwinds_case_error_handlers(self):
        _, compiled = self.compile("""fn main() {
 for number in [1, 2, 3] {
  match Some(number) {
   Some(value) => {
    try {
     if value == 1 { continue }
     if value == 3 { break }
     print(value)
    } catch error { print(999) }
   }
   None => {}
  }
 }
 try { print(jsonDecode[int]("true")) }
 catch error { print(error.code) }
}""")
        self.assertEqual(VM(compiled.bytecode).run(), [2, "json.type_mismatch"])

    def test_multifile_generic_enum_and_function(self):
        files = {
            "model.pxl": "export enum Box[T] { Empty, Value(item: T) }",
            "main.pxl": """import "model.pxl" as model
fn extract[T](value: model.Box[T], fallback: T) -> T = match value {
 model.Box.Empty => fallback, model.Box.Value(item) => item
}
fn main() { print(extract[int](model.Box[int].Value(42), 0)) }""",
        }
        source, compiled = compile_project(files)
        self.assertEqual(VM(compiled.bytecode).run(), [42])
        self.assertEqual(
            VM(compile_source(decode_picture(encode_picture(source))).bytecode).run(),
            [42],
        )

    def test_bytecode_rejects_invalid_schema_and_variant(self):
        _, compiled = self.compile("fn main() { print(Some(42)) }")
        for records in (None, 1, [[]]):
            bc = copy.deepcopy(compiled.bytecode)
            bc["records"] = records
            with self.subTest(records=records), self.assertRaises(PixelError):
                validate_bytecode(bc)
        bc = copy.deepcopy(compiled.bytecode)
        bc["enums"]["Option<int>"]["Some"]["value"] = "bool"
        with self.assertRaises(PixelError):
            validate_bytecode(bc)


if __name__ == "__main__":
    unittest.main()
