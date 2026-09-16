import unittest

from pixellang.editor import SymbolIndex
from pixellang.model import PixelError
from pixellang.picture import decode_picture, encode_picture
from pixellang.project import compile_project, project_text
from pixellang.vm import VM


class IterationV5Tests(unittest.TestCase):
    def run_source(self, source):
        image, result = compile_project({"main.pxl": source})
        output = VM(result.bytecode).run()
        recovered = project_text(decode_picture(encode_picture(image)))
        self.assertEqual(
            VM(compile_project(recovered["files"])[1].bytecode).run(), output
        )
        return output

    def test_array_string_and_empty_pairs(self):
        self.assertEqual(
            self.run_source("""fn main() {
 for index, value in [4, 9] { print(index); print(value) }
 for index, value in "你🙂" { print(index); print(value) }
 let empty: [int] = []
 for index, value in empty { print(value) }
}"""),
            [0, 4, 1, 9, 0, "你", 1, "🙂"],
        )

    def test_map_keys_and_values_are_snapshotted(self):
        self.assertEqual(
            self.run_source("""fn main() {
 let values = map[int]{"a": 1, "b": 2}
 for key, value in values {
  print(key); print(value)
  values["b"] = 99
  delete(values, "a")
  values["c"] = 3
 }
}"""),
            ["a", 1, "b", 2],
        )

    def test_array_snapshot_is_shallow(self):
        self.assertEqual(
            self.run_source("""record Row { amount: int }
fn main() {
 let rows = [Row{amount: 1}, Row{amount: 2}]
 for index, row in rows {
  print(row.amount)
  rows[1].amount = 9
  rows[1] = Row{amount: 100}
  append(rows, Row{amount: 20})
 }
}"""),
            [1, 9],
        )

    def test_pair_bindings_immutable_and_distinct(self):
        for body in ("key = 0", "value = 0"):
            with self.subTest(body=body), self.assertRaises(PixelError):
                compile_project(
                    {"main.pxl": "fn main() { for key, value in [1] { " + body + " } }"}
                )
        with self.assertRaises(PixelError):
            compile_project({"main.pxl": "fn main() { for item, item in [1] {} }"})

    def test_editor_tracks_both_bindings(self):
        source = "fn main() { for index, value in [1] { print(index); print(value) } }"
        index = SymbolIndex({"main.pxl": source})
        self.assertEqual(len(index.references("main.pxl", source.index("index"))), 2)
        self.assertEqual(len(index.references("main.pxl", source.index("value"))), 2)
        index.rename("main.pxl", source.index("value"), "element")
