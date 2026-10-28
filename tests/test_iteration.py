import unittest

from pixellang.native_editor import NativeSymbolIndex
from pixellang.compiler_client import CompilerClient
from pixellang.model import PixelError
from pixellang.vm import VM


class IterationTests(unittest.TestCase):
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

    def run_source(self, source):
        files = {"main.pxl": source}
        output = VM(self.request('compile', files=files)['bytecode']).run()
        image = self.request('export-png', files=files)['image']
        recovered = self.request('recover-png', image=image)
        rebuilt = self.request('compile', **recovered)['bytecode']
        self.assertEqual(VM(rebuilt).run(), output)
        return output

    def test_array_string_and_empty_pairs(self):
        self.assertEqual(
            self.run_source("""effect fn main() {
 for index, value in [4, 9] { print(index); print(value) }
 for index, value in "你🙂" { print(index); print(value) }
 let empty: [int] = []
 for index, value in empty { print(value) }
}"""),
            [0, 4, 1, 9, 0, "你", 1, "🙂"],
        )

    def test_map_keys_and_values_are_snapshotted(self):
        self.assertEqual(
            self.run_source("""effect fn main() {
 var values: map[string, int] = map[string, int]{"a": 1, "b": 2}
 for key, value in values {
  print(key); print(value)
  values["b"] = 99
  values.remove("a")
  values["c"] = 3
 }
}"""),
            ["a", 1, "b", 2],
        )

    def test_array_snapshot_preserves_nested_values(self):
        self.assertEqual(
            self.run_source("""record Row { amount: int }
effect fn main() {
 var rows: [Row] = [Row{amount: 1}, Row{amount: 2}]
 for index, row in rows {
  print(row.amount)
  rows[1].amount = 9
  rows[1] = Row{amount: 100}
  rows.append(Row{amount: 20})
 }
}"""),
            [1, 2],
        )

    def test_pair_bindings_immutable_and_distinct(self):
        for body in ("key = 0", "value = 0"):
            with self.subTest(body=body), self.assertRaises(PixelError):
                self.request("compile", files=
                    {"main.pxl": "effect fn main() { for key, value in [1] { " + body + " } }"}
                )
        with self.assertRaises(PixelError):
            self.request("compile", files={"main.pxl": "effect fn main() { for item, item in [1] {} }"})

    def test_editor_tracks_both_bindings(self):
        source = "effect fn main() { for index, value in [1] { print(index); print(value) } }"
        index = self.index({"main.pxl": source})
        self.assertEqual(len(index.references("main.pxl", source.index("index"))), 2)
        self.assertEqual(len(index.references("main.pxl", source.index("value"))), 2)
        index.rename("main.pxl", source.index("value"), "element", analyze=self.index)
