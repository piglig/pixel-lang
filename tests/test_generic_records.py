import unittest

from pixellang.native_editor import NativeSymbolIndex
from pixellang.model import PixelError
from pixellang.compiler_client import CompilerClient
from pixellang.temporal import Timeline
from pixellang.vm import VM


class GenericRecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def request(self, operation, **args):
        response = self.compiler.request(operation, **args)
        self.assertEqual(response['status'], 'ok', response)
        return response['result']

    def index(self, files):
        return NativeSymbolIndex(files, 'main.pxl', self.request('editor-index', files=files)['index'])

    def complete(self, code, offset):
        return self.index({'main.pxl': code}).complete('main.pxl', offset, analyze=self.index)

    def test_constructor_infers_fields_and_expected_type_through_png(self):
        files = {'main.pxl': """record Box[T] { value: T }
effect fn main() {
 let number = Box{value: 42}
 let items: Box[[int]] = Box{value: []}
 print(number.value)
 print(items.value)
}"""}
        expected = [42, []]
        self.assertEqual(VM(self.request('compile', files=files)['bytecode']).run(), expected)
        image = self.request('export-png', files=files)['image']
        recovered = self.request('recover-png', image=image)
        self.assertEqual(VM(self.request('compile', **recovered)['bytecode']).run(), expected)

    def test_concrete_types_remain_distinct_after_image_roundtrip(self):
        files = {
            "main.pxl": """record Box[T] { value: T }
effect fn main() {
 let i = Box::[int]{value: 42}
 let s = Box::[string]{value: "cat"}
 var nested: Box[[Box[float64]]] = Box::[[Box[float64]]]{value: [Box::[float64]{value: 1.25}]}
 nested.value[0].value += 0.5
 print(i); print(s); print(nested)
}"""
        }
        compiled = self.request("compile", files=files)["bytecode"]
        expected = [{"value": 42}, {"value": "cat"}, {"value": [{"value": 1.75}]}]
        self.assertEqual(VM(compiled).run(), expected)
        self.assertFalse(
            any("$" in str(schema) for schema in compiled["records"].values())
        )
        image = self.request('export-png', files=files)['image']
        self.assertEqual(VM(self.request('compile-png', image=image)['bytecode']).run(), expected)
        recovered = self.request('recover-png', image=image)
        self.assertEqual(VM(self.request('compile', **recovered)['bytecode']).run(), expected)

    def test_multifile_generic_fields_and_json(self):
        files = {
            "model.pxl": "export record Pair[A, B] { first: A, second: B }",
            "main.pxl": """import "model.pxl" as model
effect fn RunTest()->Result[int,Error] {
 let decoded = jsonDecode::[model.Pair[int, [string]]]("{\\"first\\":3,\\"second\\":[\\"x\\"]}")
 let pair = (decoded)?
 print(pair.first); print(pair.second)

 return .Ok(0)
}
effect fn main(){match RunTest(){.Ok(done)=>{},.Err(error)=>{print(error)}}}
""",
        }
        compiled = self.request("compile", files=files)["bytecode"]
        self.assertEqual(VM(compiled).run(), [3, ["x"]])
        source = self.request('pixels', files=files)['document']
        recovered = self.request('recover-pixels', document=source)
        self.assertEqual(VM(self.request('compile', **recovered)['bytecode']).run(), [3, ['x']])

    def test_regular_recursive_generic_data_and_replay(self):
        compiled = self.compiler.debug_project(
            {
                "main.pxl": """record Node[T] { value: T, children: [Node[T]] }
effect fn main() {
 var root: Node[int] = Node::[int]{value: 1, children: []}
 root.children.append(Node::[int]{value: 2, children: []})
 root.children[0].value += 3
 print(root)
}"""
            }
        )
        timeline = Timeline(compiled.source, compiled=compiled)
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
            "record Box[T] { value: T }\neffect fn main() { let b = Box{} }",
            "record Box[T] { value: T }\neffect fn main() { let b = Box::[int, string]{value: 1} }",
            "record Box[T] { value: T }\neffect fn main() { let b = Box::[int]{value: true} }",
            "record Box[T] { value: T }\neffect fn main() { let b: Box[string] = Box::[int]{value: 1} }",
            "record Box[T, T] { value: T }\neffect fn main() {}",
            "record Box[T] { value: T }\neffect fn main() { let x: T = 1 }",
            "record Box[T] { value: Missing }\neffect fn main() {}",
            "record Grow[T] { children: [Grow[[T]]] }\neffect fn main() { let g = Grow::[int]{children: []} }",
        ]
        for code in bad:
            with self.subTest(code=code), self.assertRaises(PixelError):
                self.request("compile", files={"main.pxl": code})
        with self.assertRaisesRegex(PixelError, "private"):
            self.request("compile", files={
                    "main.pxl": 'import "hidden.pxl"\neffect fn main() { let b = hidden.Box::[int]{value: 1} }',
                    "hidden.pxl": "record Box[T] { value: T }",
                }
            )

    def test_parameter_and_specialized_field_navigation_completion_and_rename(self):
        code = "record Box[T] { value: T }\neffect fn main() { let box = Box::[int]{value: 42}; print(box.value) }"
        index = self.index({"main.pxl": code})
        parameter = index.at("main.pxl", code.index("T }"))
        self.assertEqual(index.symbols[parameter["symbol"]]["kind"], "type_parameter")
        field = index.at("main.pxl", code.rindex("value"))
        self.assertEqual(
            index.symbols[field["symbol"]]["span"]["start"]["offset"], code.index("value")
        )
        proposals = self.complete(code, code.rindex("value") + 2)
        self.assertEqual(
            proposals, [{"label": "value", "kind": "Field", "detail": "int"}]
        )
        edits = index.rename("main.pxl", code.index("T }"), "Item", analyze=self.index)
        self.assertEqual(len(edits), 2)
        self.assertTrue(all(edit["text"] == "Item" for edit in edits))

    def test_type_parameter_completion_and_scope_isolation(self):
        code = "record Box[Item] { value: It }\neffect fn main() {}"
        result = self.complete(code, code.index("It }") + 2)
        self.assertEqual(
            result,
            [{"label": "Item", "kind": "TypeParameter", "detail": "type parameter"}],
        )
        code = "record Box[T] { value: T }\nrecord Other[T] { value: T }\neffect fn main() {}"
        index = self.index({"main.pxl": code})
        first = index.at("main.pxl", code.index("T }"))
        second = index.at("main.pxl", code.rindex("T }"))
        self.assertNotEqual(first["symbol"], second["symbol"])
        self.assertEqual(len(index.rename("main.pxl", code.index("T }"), "Element", analyze=self.index)), 2)


if __name__ == "__main__":
    unittest.main()
