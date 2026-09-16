import unittest

from pixellang.editor import SymbolIndex, complete, format_source
from pixellang.workstation import Workstation


class EditorTests(unittest.TestCase):
    def test_shadowing_and_compound_assignment(self):
        source = """fn main() {
 let value = 1
 if true {
  var value = 2
  value += 1
  print(value)
 }
 print(value)
 print("value") // value
}
"""
        index = SymbolIndex({"main.pxl": source})
        outer = index.references("main.pxl", source.index("value"))
        inner = index.references("main.pxl", source.index("value = 2"))
        self.assertEqual(len(outer), 2)
        self.assertEqual(len(inner), 3)
        self.assertEqual(
            index.definition("main.pxl", source.rindex("print(value)") + 6), outer[0]
        )
        self.assertIsNone(index.definition("main.pxl", source.index('"value"') + 1))

    def test_cross_file_records_fields_and_aliases(self):
        files = {
            "main.pxl": """import "models.pxl" as model
fn main() {
 let item = model.Item{label: "你好"}
 item.label = model.Label(item)
 print(item.label)
}
""",
            "models.pxl": """export record Item { label: string }
export fn Label(item: Item) -> string = item.label
""",
        }
        index = SymbolIndex(files)
        refs = index.references("main.pxl", files["main.pxl"].index("label:"))
        self.assertEqual(len(refs), 5)
        self.assertEqual({r["source"] for r in refs}, set(files))
        definition = index.definition("main.pxl", files["main.pxl"].index("Label("))
        self.assertEqual(definition["source"], "models.pxl")
        self.assertEqual(
            definition["start"]["offset"], files["models.pxl"].index("Label")
        )
        types = index.references("models.pxl", files["models.pxl"].index("Item"))
        self.assertEqual(len(types), 3)
        self.assertEqual(
            len(index.references("main.pxl", files["main.pxl"].index("as model") + 3)),
            3,
        )

    def test_disconnected_files_and_parameter_identity(self):
        files = {
            "main.pxl": "fn main() { print(1) }",
            "other.pxl": "fn Echo(x: int) -> int = x",
        }
        index = SymbolIndex(files)
        self.assertEqual(
            len(index.references("other.pxl", files["other.pxl"].index("x:"))), 2
        )

    def test_editor_service_uses_supplied_source(self):
        source = "fn main() { let 数字 = 1\nprint(数字) }"
        result = Workstation().request(
            "definition",
            {
                "files": {"main.pxl": source},
                "source": "main.pxl",
                "offset": source.rindex("数字"),
            },
        )
        self.assertEqual(result["start"]["offset"], source.index("数字"))

    def apply_rename(self, files, filename, offset, name):
        edits = SymbolIndex(files).rename(filename, offset, name)
        changed = dict(files)
        for edit in reversed(edits):
            span = edit["span"]
            text = changed[span["source"]]
            changed[span["source"]] = (
                text[: span["start"]["offset"]]
                + edit["text"]
                + text[span["end"]["offset"] :]
            )
        return changed

    def test_rename_preserves_comments_and_strings(self):
        text = 'fn main() { let 数字 = 1\nprint(数字) // 数字\nprint("数字") }'
        changed = self.apply_rename(
            {"main.pxl": text}, "main.pxl", text.index("数字"), "amount"
        )["main.pxl"]
        self.assertEqual(
            changed,
            'fn main() { let amount = 1\nprint(amount) // 数字\nprint("数字") }',
        )

    def test_rename_rejects_capture_in_both_directions(self):
        from pixellang.model import PixelError

        text = "fn main() { let a = 1\nif true {\n let b = 2\n print(a)\n print(b)\n} }"
        for offset, name in ((text.index("a ="), "b"), (text.index("b ="), "a")):
            with self.assertRaisesRegex(PixelError, "binding"):
                SymbolIndex({"main.pxl": text}).rename("main.pxl", offset, name)

    def test_rename_implicit_alias_keeps_import_path(self):
        files = {
            "main.pxl": 'import "util.pxl"\nfn main() { print(util.Value()) }',
            "util.pxl": "export fn Value() -> int = 42",
        }
        changed = self.apply_rename(
            files, "main.pxl", files["main.pxl"].index("util.Value"), "helper"
        )
        self.assertEqual(
            changed["main.pxl"],
            'import "util.pxl" as helper\nfn main() { print(helper.Value()) }',
        )

    def test_rename_cross_file_type_and_field(self):
        files = {
            "main.pxl": 'import "util.pxl"\nfn main() { let a = util.Row{value: 1}\nprint(a.value) }',
            "util.pxl": "export record Row { value: int }",
        }
        changed = self.apply_rename(
            files, "util.pxl", files["util.pxl"].index("Row"), "Entry"
        )
        self.assertIn("util.Entry", changed["main.pxl"])
        changed = self.apply_rename(
            changed, "util.pxl", changed["util.pxl"].index("value"), "amount"
        )
        self.assertIn("{amount: 1}", changed["main.pxl"])
        self.assertIn("a.amount", changed["main.pxl"])

    def completions(self, source, extra=None):
        offset = source.index("|")
        source = source.replace("|", "", 1)
        return complete(
            {"main.pxl": source, **(extra or {})}, "main.pxl", "main.pxl", offset
        )

    def test_completion_nested_record_and_partial_name(self):
        items = self.completions(
            'record Row { count: int, label: string }\nfn main() { let rows = [Row{count: 1, label: "a"}]\nrows[0].co|'
        )
        self.assertEqual(items, [{"label": "count", "kind": "Field", "detail": "int"}])

    def test_completion_scope_and_unfinished_block(self):
        items = self.completions(
            "fn main() {\n let local = true\n if true { let inner = 2 }\n lo|"
        )
        self.assertEqual(
            items,
            [
                {"label": "local", "kind": "Variable", "detail": "bool"},
                {"label": "lookup", "kind": "Function", "detail": "PixelLang builtin"},
            ],
        )
        items = self.completions("fn helper() { let hidden = 1 }\nfn main() {\n hi|")
        self.assertEqual(items, [])

    def test_completion_only_exported_module_members(self):
        items = self.completions(
            'import "util.pxl"\nfn main() { util.|',
            {
                "util.pxl": "export fn Public() -> int = 1\nfn private() -> int = 2\nexport record Row { value: int }"
            },
        )
        self.assertEqual([item["label"] for item in items], ["Public", "Row"])

    def test_no_completion_inside_string_or_comment(self):
        self.assertEqual(self.completions('print("value|")'), [])
        self.assertEqual(self.completions("// val|"), [])

    def test_completion_local_record_named_like_module(self):
        items = self.completions(
            'import "util.pxl"\nrecord Row { field: int }\nfn main() { let util = Row{field: 1}\nutil.|',
            {"util.pxl": "export fn Public() -> int = 1"},
        )
        self.assertEqual([item["label"] for item in items], ["field"])

    def test_completion_in_type_annotations(self):
        for code in (
            "record Row { value: int }\nfn use(item: Ro|) { return }",
            "record Row { value: int }\nfn main() { let item: Ro| = 1 }",
            "record Row { value: int }\nfn make() -> Ro| { return 1 }",
        ):
            self.assertEqual(
                self.completions(code),
                [{"label": "Row", "kind": "Struct", "detail": "record Row"}],
            )
        self.assertEqual(
            self.completions("fn main() { let count: in| = 1 }"),
            [{"label": "int", "kind": "Keyword", "detail": "type"}],
        )

    def test_completion_qualified_type_filters_private_records(self):
        result = self.completions(
            'import "lib.pxl"\nfn use(x: lib.|) { return }',
            {
                "lib.pxl": "export record Public { x: int }\nrecord private { x: int }\nfn Function() { return }"
            },
        )
        self.assertEqual([item["label"] for item in result], ["Public"])

    def test_format_preserves_comments_literals_and_line_endings(self):
        text = 'fn main() {\r\n// keep me\r\nlet data = [\r\n"}", // bracket in string\r\n"猫"\r\n]\r\n/* multiline\r\n original spacing */\r\nprint(data)\r\n}\r\n'
        formatted = format_source(text, "main.pxl")
        self.assertIn("    // keep me\r\n", formatted)
        self.assertIn('        "}", // bracket in string\r\n', formatted)
        self.assertIn("/* multiline\r\n original spacing */", formatted)
        self.assertEqual(format_source(formatted, "main.pxl"), formatted)
        from pixellang.project import compile_project
        from pixellang.vm import VM

        self.assertEqual(
            VM(compile_project({"main.pxl": formatted})[1].bytecode).run(),
            [["}", "猫"]],
        )


if __name__ == "__main__":
    unittest.main()
