import unittest

from pixellang.editor import SymbolIndex, complete
from pixellang.model import PixelError


class EnumEditorTests(unittest.TestCase):
    def test_cross_module_type_variant_and_parameter_navigation_rename(self):
        files = {
            "model.pxl": "export enum Event[T] { Stop, Data(value: T) }",
            "main.pxl": """import "model.pxl" as model
fn main() {
 let event = model.Event[int].Data(42)
 print(match event { model.Event.Stop => 0, model.Event.Data(value) => value })
}""",
        }
        index = SymbolIndex(files)
        source = files["main.pxl"]
        for token in ("Data(42)", "Data(value)"):
            target = index.definition("main.pxl", source.index(token))
            self.assertEqual(target["source"], "model.pxl")
            self.assertEqual(
                target["start"]["offset"], files["model.pxl"].index("Data")
            )
        self.assertEqual(
            len(index.rename("main.pxl", source.index("Data(42)"), "Payload")), 3
        )
        self.assertEqual(
            len(
                index.rename("model.pxl", files["model.pxl"].index("Event"), "Message")
            ),
            4,
        )
        self.assertEqual(
            len(index.rename("model.pxl", files["model.pxl"].index("T]"), "Item")), 2
        )
        use = source.rindex("value")
        self.assertEqual(
            index.definition("main.pxl", use)["start"]["offset"], source.index("value)")
        )
        self.assertEqual(len(index.rename("main.pxl", use, "item")), 2)

    def test_variant_identity_and_pattern_bindings_are_scoped(self):
        code = """enum First { Data(value: int) }
enum Second { Data(value: int) }
fn main() {
 let value = 9
 print(match First.Data(1) { First.Data(value) => value })
 print(match Second.Data(2) { Second.Data(value) => value })
 print(value)
}"""
        index = SymbolIndex({"main.pxl": code})
        self.assertEqual(len(index.rename("main.pxl", code.index("Data"), "Item")), 3)
        self.assertEqual(
            len(index.rename("main.pxl", code.index("value ="), "outside")), 2
        )
        self.assertEqual(
            len(index.rename("main.pxl", code.index("value) =>"), "inside")), 2
        )

    def test_variant_rename_collision_rejected(self):
        code = (
            "enum Event { Stop, Data(value: int) }\nfn main() { print(Event.Data(1)) }"
        )
        with self.assertRaises(PixelError):
            SymbolIndex({"main.pxl": code}).rename(
                "main.pxl", code.index("Data"), "Stop"
            )

    def proposals(self, code, extra=None):
        offset = code.index("|")
        return complete(
            {"main.pxl": code.replace("|", ""), **(extra or {})},
            "main.pxl",
            "main.pxl",
            offset,
        )

    def test_incomplete_generic_and_option_constructors(self):
        self.assertEqual(
            self.proposals(
                "enum Event[T] { Stop, Data(value: T) }\nfn main() { let e = Event[int].D|"
            ),
            [{"label": "Data", "kind": "EnumMember", "detail": "Data(value: int)"}],
        )
        self.assertEqual(
            self.proposals("fn main() { let e = Option[int].S|"),
            [{"label": "Some", "kind": "EnumMember", "detail": "Some(value: int)"}],
        )
        self.assertEqual(
            self.proposals(
                'import "model.pxl" as model\nfn main() { let e = model.Event[string].D|',
                {"model.pxl": "export enum Event[T] { Data(value: T) }"},
            ),
            [{"label": "Data", "kind": "EnumMember", "detail": "Data(value: string)"}],
        )

    def test_unfinished_match_arm_completion(self):
        self.assertEqual(
            self.proposals("fn main() { match Some(1) { So|"),
            [{"label": "Some", "kind": "EnumMember", "detail": "Some(value: int)"}],
        )
        self.assertEqual(
            self.proposals(
                "enum Event { Stop, Data(value: int) }\nfn main() { match Event.Stop { Event.D|"
            ),
            [{"label": "Data", "kind": "EnumMember", "detail": "Data(value: int)"}],
        )

    def test_type_completion_and_branch_local_members(self):
        self.assertEqual(
            self.proposals("enum Event { Stop }\nfn main() { let e: Ev|"),
            [{"label": "Event", "kind": "Enum", "detail": "enum Event"}],
        )
        self.assertEqual(
            self.proposals(
                "record Row { amount: int }\nfn main() { match Some(Row{amount: 42}) { Some(row) => { print(row.am|) } None => {} } }"
            ),
            [{"label": "amount", "kind": "Field", "detail": "int"}],
        )


if __name__ == "__main__":
    unittest.main()
