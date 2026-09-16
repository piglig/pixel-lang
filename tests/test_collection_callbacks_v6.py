import json
import random
import unittest
from pathlib import Path

from pixellang.compiler import compile_source
from pixellang.model import PixelError
from pixellang.picture import decode_picture, encode_picture
from pixellang.project import compile_project, project_text
from pixellang.temporal import Timeline
from pixellang.vm import VM


class CollectionCallbackTests(unittest.TestCase):
    def compile(self, body, declarations=""):
        return compile_project(
            {
                "main.pxl": 'import "std/collections.pxl" as c\n'
                + declarations
                + "\nfn main() {\n"
                + body
                + "\n}"
            }
        )

    def test_sort_matches_independent_oracle(self):
        _, compiled = self.compile("""let values = jsonDecode[[int]](input())
print(c.Sort[int](values, fn(a: int, b: int) -> bool { return a < b }))
print(values)""")
        randomizer = random.Random(42)
        arrays = [[], [1], [3, 3, 3], list(range(25)), list(reversed(range(25)))]
        arrays += [
            [randomizer.randrange(-20, 21) for _ in range(size)]
            for size in [2, 3, 7, 16, 31, 64, 129]
        ]
        for values in arrays:
            with self.subTest(size=len(values)):
                self.assertEqual(
                    VM(
                        compiled.bytecode,
                        input_text=json.dumps(values),
                        max_steps=1_000_000,
                    ).run(),
                    [sorted(values), values],
                )

    def test_stable_record_sort_retains_shared_elements(self):
        _, compiled = self.compile(
            """let rows = [Row{key: 2, name: "a"}, Row{key: 1, name: "b"}, Row{key: 2, name: "c"}]
let sorted = c.Sort[Row](rows, fn(a: Row, b: Row) -> bool { return a.key < b.key })
print(c.Map[Row, string](sorted, fn(row: Row) -> string { return row.name }))
sorted[0].name = "changed"
print(rows[1].name)""",
            "record Row { key: int, name: string }",
        )
        self.assertEqual(VM(compiled.bytecode).run(), [["b", "a", "c"], "changed"])

    def test_map_filter_fold_group_and_capture_image_replay(self):
        source, compiled = self.compile("""let threshold = 2
let values = c.Filter[int]([1, 4, 2, 3], fn(value: int) -> bool { return value > threshold })
let scaled = c.Map[int, float64](values, fn(value: int) -> float64 { return float64(value) / 2.0 })
print(c.Fold[float64, float64](scaled, 0.0, fn(total: float64, value: float64) -> float64 { return total + value }))
let grouped = c.GroupBy[string](["bee", "ant", "bat"], fn(value: string) -> string { return value[0] })
print(keys(grouped)); print(grouped["b"])""")
        expected = [3.5, ["b", "a"], ["bee", "bat"]]
        self.assertEqual(VM(compiled.bytecode).run(), expected)
        image = decode_picture(encode_picture(source))
        self.assertEqual(VM(compile_source(image).bytecode).run(), expected)
        self.assertEqual(
            VM(compile_project(project_text(image)["files"])[1].bytecode).run(),
            expected,
        )
        timeline = Timeline(image)
        states = [timeline.vm.state()]
        while not timeline.vm.halted:
            timeline.advance()
            states.append(timeline.vm.state())
        restored = Timeline.restore(timeline.document())
        for z in reversed(range(len(states))):
            self.assertEqual(restored.seek(z), states[z])

    def test_empty_inputs_do_not_invoke_callbacks(self):
        _, compiled = self.compile("""var calls = 0
let values: [int] = []
print(c.Map[int, int](values, fn(n: int) -> int { calls += 1; return n }))
print(c.Filter[int](values, fn(n: int) -> bool { calls += 1; return true }))
print(c.Fold[int, int](values, 42, fn(a: int, b: int) -> int { calls += 1; return a + b }))
print(c.GroupBy[int](values, fn(n: int) -> string { calls += 1; return string(n) }))
print(c.Sort[int](values, fn(a: int, b: int) -> bool { calls += 1; return a < b }))
print(calls)""")
        self.assertEqual(VM(compiled.bytecode).run(), [[], [], 42, {}, [], 0])

    def test_entry_snapshot_and_callback_failure(self):
        _, compiled = self.compile("""let values = [1, 2]
print(c.Map[int, int](values, fn(n: int) -> int { append(values, 99); return n }))
print(values)
var calls = 0
try {
 print(c.Map[int, int]([1, 2, 3], fn(n: int) -> int { calls += 1; if n == 2 { fail("stop") }; return n }))
} catch error { print(error.message) }
print(calls)""")
        self.assertEqual(
            VM(compiled.bytecode).run(), [[1, 2], [1, 2, 99, 99], "stop", 2]
        )

    def test_multifile_example_png_only(self):
        root = Path(__file__).resolve().parents[1] / "examples" / "callback-analysis"
        files = {path.name: path.read_text() for path in root.glob("*.pxl")}
        source, compiled = compile_project(files)
        expected = json.loads((root / "expected.json").read_text())
        input_text = (root / "input.json").read_text()
        self.assertEqual(VM(compiled.bytecode, input_text=input_text).run(), expected)
        recovered = compile_source(decode_picture(encode_picture(source)))
        self.assertEqual(VM(recovered.bytecode, input_text=input_text).run(), expected)

    def test_bad_callback_signatures_rejected(self):
        for body in [
            "print(c.Sort[int]([1], fn(a: int) -> bool { return true }))",
            "print(c.Map[int, string]([1], fn(a: int) -> int { return a }))",
            "print(c.Filter[int]([1], fn(a: bool) -> bool { return a }))",
            "print(c.GroupBy[int]([1], fn(a: int) -> int { return a }))",
        ]:
            with self.subTest(body=body), self.assertRaises(PixelError):
                self.compile(body)


if __name__ == "__main__":
    unittest.main()
