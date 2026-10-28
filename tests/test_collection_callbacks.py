import json
import random
import unittest
from pathlib import Path

from pixellang.model import PixelError
from pixellang.compiler_client import CompilerClient
from pixellang.temporal import Timeline
from pixellang.vm import VM


class CollectionCallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def request(self, operation, **args):
        response = self.compiler.request(operation, **args)
        self.assertEqual(response['status'], 'ok', response)
        return response['result']

    def compile(self, body, declarations=""):
        return self.request(
            "compile", files={
                "main.pxl": 'import "std/collections.pxl" as c\n'
                + declarations
                + "\neffect fn main() {\n"
                + body
                + "\n}"
            }
        )["bytecode"]

    def test_sort_matches_independent_oracle(self):
        compiled = self.compile("""match jsonDecode::[[int]](input()) { .Ok(values) => {
print(c.SortedBy::[int](values, fn(a: int, b: int) -> bool { return a < b }))
print(values) }, .Err(error) => { print(error) } }""")
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
                        compiled,
                        input_text=json.dumps(values),
                        max_steps=1_000_000,
                    ).run(),
                    [sorted(values), values],
                )

    def test_stable_record_sort_and_value_copy_isolation(self):
        compiled = self.compile(
            """let rows = [Row{key: 2, name: "a"}, Row{key: 1, name: "b"}, Row{key: 2, name: "c"}]
let sorted = c.SortedBy::[Row](rows, fn(a: Row, b: Row) -> bool { return a.key < b.key })
print(c.Map::[Row, string](sorted, fn(row: Row) -> string { return row.name }))
var edited = sorted
edited[0].name = "changed"
print(rows[1].name); print(sorted[0].name); print(edited[0].name)""",
            "record Row { key: int, name: string }",
        )
        self.assertEqual(VM(compiled).run(), [["b", "a", "c"], "b", "b", "changed"])

    def test_map_filter_fold_group_and_capture_image_replay(self):
        body = '''let threshold = 2
let values = c.Filter::[int]([1, 4, 2, 3], fn(value: int) -> bool { return value > threshold })
let scaled = c.Map::[int, float64](values, fn(value: int) -> float64 { return float64(value) / 2.0 })
print(c.Fold::[float64, float64](scaled, 0.0, fn(total: float64, value: float64) -> float64 { return total + value }))
let grouped = c.GroupBy::[string, string](["bee", "ant", "bat"], fn(value: string) -> string { return value[0] })
print(grouped.keys()); print(grouped["b"])'''
        compiled = self.compiler.debug_project({"main.pxl": 'import "std/collections.pxl" as c\neffect fn main() {\n' + body + '\n}'})
        expected = [3.5, ["b", "a"], ["bee", "bat"]]
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
        restored = Timeline.restore(timeline.document())
        for z in reversed(range(len(states))):
            self.assertEqual(restored.seek(z), states[z])

    def test_empty_inputs_do_not_invoke_callbacks(self):
        compiled = self.compile("""let values: [int] = []
print(c.Map::[int, int](values, fn(n: int) -> int { let empty: [int] = []; return empty[0] }))
print(c.Filter::[int](values, fn(n: int) -> bool { let empty: [bool] = []; return empty[0] }))
print(c.Fold::[int, int](values, 42, fn(a: int, b: int) -> int { let empty: [int] = []; return empty[0] }))
print(c.GroupBy::[int, string](values, fn(n: int) -> string { let empty: [string] = []; return empty[0] }))
print(c.SortedBy::[int](values, fn(a: int, b: int) -> bool { let empty: [bool] = []; return empty[0] }))""")
        self.assertEqual(VM(compiled).run(), [[], [], 42, {}, []])

    def test_input_snapshot_and_callback_failure(self):
        compiled = self.compile("""var builder: [int] = [1, 2]
let values = builder
builder.append(99)
print(c.Map::[int, int](values, fn(n: int) -> int = n))
print(values); print(builder)
print(c.Map::[int, int]([1, 2, 3], fn(n: int) -> int {
 if n == 2 { let empty: [int] = []; return empty[0] }; return n
}))""")
        vm = VM(compiled)
        with self.assertRaises(PixelError):
            vm.run()
        self.assertEqual(vm.output, [[1, 2], [1, 2], [1, 2, 99]])

    def test_callbacks_cannot_mutate_captured_state(self):
        for body in [
            'var values: [int] = [1]; print(c.Map::[int,int]([1], fn(n:int)->int { values.append(99); return n }))',
            'var calls = 0; print(c.Map::[int,int]([1], fn(n:int)->int { calls += 1; return n }))',
        ]:
            with self.subTest(body=body), self.assertRaises(PixelError):
                self.compile(body)

    def test_multifile_example_png_only(self):
        root = Path(__file__).resolve().parents[1] / "examples" / "callback-analysis"
        files = {path.name: path.read_text() for path in root.glob("*.pxl")}
        compiled = self.request("compile", files=files)["bytecode"]
        expected = json.loads((root / "expected.json").read_text())
        input_text = (root / "input.json").read_text()
        self.assertEqual(VM(compiled, input_text=input_text).run(), expected)
        image = self.request("export-png", files=files)["image"]
        recovered = self.request("compile-png", image=image)["bytecode"]
        self.assertEqual(VM(recovered, input_text=input_text).run(), expected)
        sources = self.request("recover-png", image=image)
        rebuilt = self.request("compile", **sources)["bytecode"]
        self.assertEqual(VM(rebuilt, input_text=input_text).run(), expected)

    def test_bad_callback_signatures_rejected(self):
        for body in [
            "print(c.SortedBy::[int]([1], fn(a: int) -> bool { return true }))",
            "print(c.Map::[int, string]([1], fn(a: int) -> int { return a }))",
            "print(c.Filter::[int]([1], fn(a: bool) -> bool { return a }))",
            "print(c.GroupBy::[int, string]([1], fn(a: int) -> int { return a }))",
        ]:
            with self.subTest(body=body), self.assertRaises(PixelError):
                self.compile(body)


if __name__ == "__main__":
    unittest.main()
