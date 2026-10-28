import unittest

from tests.editor_support import NativeEditorCase
from pixellang.model import PixelError


class EnumEditorTests(NativeEditorCase):
    def test_cross_module_type_variant_and_parameter_navigation_rename(self):
        files = {
            "model.pxl": "export enum Event[T] { Stop, Data(value: T) }",
            "main.pxl": """import "model.pxl" as model
effect fn main() {
 let event = model.Event::[int].Data(42)
 print(match event { model.Event.Stop => 0, model.Event.Data(value) => value })
}""",
        }
        index = self.index(files)
        source = files["main.pxl"]
        for token in ("Data(42)", "Data(value)"):
            target = index.definition("main.pxl", source.index(token))
            self.assertEqual(target["source"], "model.pxl")
            self.assertEqual(
                target["start"]["offset"], files["model.pxl"].index("Data")
            )
        self.assertEqual(
            len(index.rename("main.pxl", source.index("Data(42)"), "Payload", analyze=self.index)), 3
        )
        self.assertEqual(
            len(
                index.rename("model.pxl", files["model.pxl"].index("Event"), "Message", analyze=self.index)
            ),
            4,
        )
        self.assertEqual(
            len(index.rename("model.pxl", files["model.pxl"].index("T]"), "Item", analyze=self.index)), 2
        )
        use = source.rindex("value")
        self.assertEqual(
            index.definition("main.pxl", use)["start"]["offset"], source.index("value)")
        )
        self.assertEqual(len(index.rename("main.pxl", use, "item", analyze=self.index)), 2)

    def test_variant_identity_and_pattern_bindings_are_scoped(self):
        code = """enum First { Data(value: int) }
enum Second { Data(value: int) }
effect fn main() {
 let value = 9
 print(match First.Data(1) { First.Data(value) => value })
 print(match Second.Data(2) { Second.Data(value) => value })
 print(value)
}"""
        index = self.index({"main.pxl": code})
        self.assertEqual(len(index.rename("main.pxl", code.index("Data"), "Item", analyze=self.index)), 3)
        self.assertEqual(
            len(index.rename("main.pxl", code.index("value ="), "outside", analyze=self.index)), 2
        )
        self.assertEqual(
            len(index.rename("main.pxl", code.index("value) =>"), "inside", analyze=self.index)), 2
        )

    def test_variant_rename_collision_rejected(self):
        code = (
            "enum Event { Stop, Data(value: int) }\neffect fn main() { print(Event.Data(1)) }"
        )
        with self.assertRaises(PixelError):
            self.index({"main.pxl": code}).rename(
                "main.pxl", code.index("Data"), "Stop"
            , analyze=self.index)

    def proposals(self, code, extra=None):
        offset = code.index("|")
        return self.complete(
            {"main.pxl": code.replace("|", ""), **(extra or {})},
            "main.pxl",
            offset,
        )

    def test_incomplete_generic_and_option_constructors(self):
        self.assertEqual(
            self.proposals(
                "enum Event[T] { Stop, Data(value: T) }\neffect fn main() { let e = Event::[int].D|"
            ),
            [{"label": "Data", "kind": "EnumMember", "detail": "Data(value: int)"}],
        )
        self.assertEqual(
            self.proposals("effect fn main() { let e = Option::[int].S|"),
            [{"label": "Some", "kind": "EnumMember", "detail": "Some(value: int)"}],
        )
        self.assertEqual(
            self.proposals(
                'import "model.pxl" as model\neffect fn main() { let e = model.Event::[string].D|',
                {"model.pxl": "export enum Event[T] { Data(value: T) }"},
            ),
            [{"label": "Data", "kind": "EnumMember", "detail": "Data(value: string)"}],
        )

    def test_unfinished_match_arm_completion(self):
        self.assertEqual(
            self.proposals('effect fn main() { match Option.Some(1) { .So|'),
            [{"label": "Some", "kind": "EnumMember", "detail": "Some(value: int)"}],
        )
        self.assertEqual(
            self.proposals(
                "enum Event { Stop, Data(value: int) }\neffect fn main() { match Event.Stop { Event.D|"
            ),
            [{"label": "Data", "kind": "EnumMember", "detail": "Data(value: int)"}],
        )

    def test_type_completion_and_branch_local_members(self):
        self.assertEqual(
            self.proposals("enum Event { Stop }\neffect fn main() { let e: Ev|"),
            [{"label": "Event", "kind": "Enum", "detail": "enum Event"}],
        )
        self.assertEqual(
            self.proposals(
                'record Row { amount: int }\neffect fn main() { match Option.Some(Row{amount: 42}) { Option.Some(row) => { print(row.am|) } Option.None => {} } }'
            ),
            [{"label": "amount", "kind": "Field", "detail": "int"}],
        )


if __name__ == "__main__":
    unittest.main()
