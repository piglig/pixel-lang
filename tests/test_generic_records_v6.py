import unittest

from pixellang.compiler import compile_source
from pixellang.editor import SymbolIndex, complete
from pixellang.model import PixelError
from pixellang.picture import decode_picture, encode_picture
from pixellang.project import compile_project, project_text
from pixellang.temporal import Timeline
from pixellang.vm import VM


class GenericRecordTests(unittest.TestCase):
    def test_concrete_types_remain_distinct_after_image_roundtrip(self):
        files = {
            "main.pxl": """record Box[T] { value: T }
fn main() {
 let i = Box[int]{value: 42}
 let s = Box[string]{value: "cat"}
 let nested = Box[[Box[float64]]]{value: [Box[float64]{value: 1.25}]}
 nested.value[0].value += 0.5
 print(i); print(s); print(nested)
}"""
        }
        source, compiled = compile_project(files)
        expected = [{"value": 42}, {"value": "cat"}, {"value": [{"value": 1.75}]}]
        self.assertEqual(VM(compiled.bytecode).run(), expected)
        self.assertFalse(
            any("$" in str(schema) for schema in compiled.bytecode["records"].values())
        )
        image = decode_picture(encode_picture(source))
        self.assertEqual(VM(compile_source(image).bytecode).run(), expected)
        recovered = project_text(image)["files"]
        self.assertIn("record Box[T]", recovered["main.pxl"])
        self.assertEqual(VM(compile_project(recovered)[1].bytecode).run(), expected)

    def test_multifile_generic_fields_and_json(self):
        files = {
            "model.pxl": "export record Pair[A, B] { first: A, second: B }",
            "main.pxl": """import "model.pxl" as model
fn main() {
 let pair = jsonDecode[model.Pair[int, [string]]]("{\\"first\\":3,\\"second\\":[\\"x\\"]}")
 print(pair.first); print(pair.second)
}""",
        }
        source, compiled = compile_project(files)
        self.assertEqual(VM(compiled.bytecode).run(), [3, ["x"]])
        self.assertEqual(
            VM(compile_project(project_text(source)["files"])[1].bytecode).run(),
            [3, ["x"]],
        )

    def test_regular_recursive_generic_data_and_replay(self):
        source, _ = compile_project(
            {
                "main.pxl": """record Node[T] { value: T, children: [Node[T]] }
fn main() {
 let root = Node[int]{value: 1, children: []}
 append(root.children, Node[int]{value: 2, children: []})
 root.children[0].value += 3
 print(root)
}"""
            }
        )
        timeline = Timeline(source)
        snapshots = [timeline.vm.state()]
        while not timeline.vm.halted:
            timeline.advance()
            snapshots.append(timeline.vm.state())
        self.assertEqual(
            snapshots[-1]["output"],
            [{"value": 1, "children": [{"value": 5, "children": []}]}],
        )
        restored = Timeline.restore(timeline.document())
        for z in reversed(range(len(snapshots))):
            self.assertEqual(restored.seek(z), snapshots[z])

    def test_bad_arguments_scopes_and_nominal_assignment_rejected(self):
        bad = [
            "record Box[T] { value: T }\nfn main() { let b = Box{value: 1} }",
            "record Box[T] { value: T }\nfn main() { let b = Box[int, string]{value: 1} }",
            "record Box[T] { value: T }\nfn main() { let b = Box[int]{value: true} }",
            "record Box[T] { value: T }\nfn main() { let b: Box[string] = Box[int]{value: 1} }",
            "record Box[T, T] { value: T }\nfn main() {}",
            "record Box[T] { value: T }\nfn main() { let x: T = 1 }",
            "record Box[T] { value: Missing }\nfn main() {}",
            "record Grow[T] { children: [Grow[[T]]] }\nfn main() { let g = Grow[int]{children: []} }",
        ]
        for code in bad:
            with self.subTest(code=code), self.assertRaises(PixelError):
                compile_project({"main.pxl": code})
        with self.assertRaisesRegex(PixelError, "private"):
            compile_project(
                {
                    "main.pxl": 'import "hidden.pxl"\nfn main() { let b = hidden.Box[int]{value: 1} }',
                    "hidden.pxl": "record Box[T] { value: T }",
                }
            )

    def test_parameter_and_specialized_field_navigation_completion_and_rename(self):
        code = "record Box[T] { value: T }\nfn main() { let box = Box[int]{value: 42}; print(box.value) }"
        index = SymbolIndex({"main.pxl": code})
        parameter = index.at("main.pxl", code.index("T }"))
        self.assertEqual(index.symbols[parameter["symbol"]]["kind"], "type_parameter")
        field = index.at("main.pxl", code.rindex("value"))
        self.assertEqual(
            index.symbols[field["symbol"]]["anchor"][1], code.index("value")
        )
        proposals = complete(
            {"main.pxl": code}, "main.pxl", "main.pxl", code.rindex("value") + 2
        )
        self.assertEqual(
            proposals, [{"label": "value", "kind": "Field", "detail": "int"}]
        )
        edits = index.rename("main.pxl", code.index("T }"), "Item")
        self.assertEqual(len(edits), 2)
        self.assertTrue(all(edit["text"] == "Item" for edit in edits))

    def test_type_parameter_completion_and_scope_isolation(self):
        code = "record Box[Item] { value: It }\nfn main() {}"
        result = complete(
            {"main.pxl": code}, "main.pxl", "main.pxl", code.index("It }") + 2
        )
        self.assertEqual(
            result,
            [{"label": "Item", "kind": "TypeParameter", "detail": "type parameter"}],
        )
        code = "record Box[T] { value: T }\nrecord Other[T] { value: T }\nfn main() {}"
        index = SymbolIndex({"main.pxl": code})
        first = index.at("main.pxl", code.index("T }"))
        second = index.at("main.pxl", code.rindex("T }"))
        self.assertNotEqual(first["symbol"], second["symbol"])
        self.assertEqual(len(index.rename("main.pxl", code.index("T }"), "Element")), 2)


if __name__ == "__main__":
    unittest.main()
