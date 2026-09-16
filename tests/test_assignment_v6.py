import unittest

from pixellang.compiler import compile_source
from pixellang.model import PixelError
from pixellang.picture import decode_picture, encode_picture
from pixellang.project import compile_project, project_text
from pixellang.temporal import Timeline
from pixellang.vm import VM


class AssignmentTests(unittest.TestCase):
    def run_code(self, code):
        return VM(compile_project({"main.pxl": code})[1].bytecode).run()

    def test_receiver_index_and_rhs_order_and_old_value(self):
        code = """record Row { amount: int }
fn receiver(rows: [Row], calls: [int]) -> [Row] {
 append(calls, 1)
 return rows
}
fn index(calls: [int]) -> int { append(calls, 2); return 0 }
fn rhs(rows: [Row], calls: [int]) -> int {
 append(calls, 3)
 rows[0].amount = 100
 return 5
}
fn main() {
 let calls: [int] = []
 let rows = [Row{amount: 10}]
 receiver(rows, calls)[index(calls)].amount += rhs(rows, calls)
 print(calls)
 print(rows[0].amount)
}"""
        source, compiled = compile_project({"main.pxl": code})
        self.assertEqual(VM(compiled.bytecode).run(), [[1, 2, 3], 15])
        pixels = decode_picture(encode_picture(source))
        self.assertEqual(VM(compile_source(pixels).bytecode).run(), [[1, 2, 3], 15])
        recovered = project_text(pixels)["files"]
        self.assertIn("+=", recovered["main.pxl"])
        self.assertEqual(
            VM(compile_project(recovered)[1].bytecode).run(), [[1, 2, 3], 15]
        )

    def test_all_locations_and_operators(self):
        self.assertEqual(
            self.run_code("""record Box { value: int }
fn main() {
 var n = 20
 let a = [20]
 let b = Box{value: 20}
 let m = map[int]{"x": 20}
 n += 4; a[0] += 4; b.value += 4; m["x"] += 4
 n -= 2; a[0] -= 2; b.value -= 2; m["x"] -= 2
 n *= 3; a[0] *= 3; b.value *= 3; m["x"] *= 3
 n /= 2; a[0] /= 2; b.value /= 2; m["x"] /= 2
 n %= 7; a[0] %= 7; b.value %= 7; m["x"] %= 7
 n++; a[0]++; b.value++; m["x"]++
 n--; a[0]--; b.value--; m["x"]--
 print(n); print(a[0]); print(b.value); print(m["x"])
 let words = ["a"]
 words[0] += "b"
 print(words[0])
}"""),
            [5, 5, 5, 5, "ab"],
        )

    def test_index_is_evaluated_once_and_snapshot_precedes_rhs(self):
        self.assertEqual(
            self.run_code("""
fn idx(calls: [int]) -> int { calls[0]++; return 0 }
fn rhs(a: [int]) -> int { a[0] = 100; return 2 }
fn main() {
 let calls = [0]
 let a = [8]
 a[idx(calls)] *= rhs(a)
 print(a); print(calls)
}"""),
            [[16], [1]],
        )

    def test_failed_read_does_not_evaluate_rhs(self):
        self.assertEqual(
            self.run_code("""
fn rhs(calls: [int]) -> int { calls[0]++; return 2 }
fn main() {
 let calls = [0]
 let m = map[int]{}
 try { m["missing"] += rhs(calls) } catch e { print("caught") }
 print(calls)
 print(len(m))
}"""),
            ["caught", [0], 0],
        )

    def test_invalid_updates_are_static_errors(self):
        for statement in [
            "let x = 1; x++",
            "let a = [true]; a[0] += false",
            'let a = ["a"]; a[0] -= "b"',
            "let a = [[1]]; a[0] += [2]",
            'try { fail("bad") } catch e { e.message += "x" }',
        ]:
            with self.subTest(statement=statement), self.assertRaises(PixelError):
                compile_project({"main.pxl": "fn main() { " + statement + " }"})

    def test_replay_restores_each_intermediate_stack_and_heap(self):
        source, _ = compile_project(
            {
                "main.pxl": """fn main() {
 let a = [3]
 a[0] *= 4
 a[0]++
 print(a)
}"""
            }
        )
        timeline = Timeline(source)
        states = [timeline.vm.state()]
        while not timeline.vm.halted:
            timeline.advance()
            states.append(timeline.vm.state())
        self.assertEqual(states[-1]["output"], [[13]])
        restored = Timeline.restore(timeline.document())
        for step in reversed(range(len(states))):
            self.assertEqual(restored.seek(step), states[step])


if __name__ == "__main__":
    unittest.main()
