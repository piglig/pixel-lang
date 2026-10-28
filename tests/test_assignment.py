import unittest

from pixellang.model import PixelError
from pixellang.compiler_client import CompilerClient
from pixellang.temporal import Timeline
from pixellang.vm import VM


class AssignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def request(self, operation, **args):
        response = self.compiler.request(operation, **args)
        self.assertEqual(response['status'], 'ok', response)
        return response['result']

    def run_code(self, code):
        return VM(self.request('compile', files={"main.pxl": code})['bytecode']).run()

    def test_index_and_rhs_order_survive_image_recovery(self):
        code = """record Row { amount: int }
fn index(calls: inout [int]) -> int { calls.append(1); return 0 }
fn rhs(calls: inout [int]) -> int { calls.append(2); return 5 }
effect fn main() {
 var calls: [int] = []
 var rows = [Row{amount: 10}]
 rows[index(&calls)].amount += rhs(&calls)
 print(calls)
 print(rows[0].amount)
}"""
        self.assertEqual(self.run_code(code), [[1, 2], 15])
        document = self.request('pixels', files={"main.pxl": code})['document']
        image = self.request('export-pixel-png', document=document)['image']
        bytecode = self.request('compile-png', image=image)['bytecode']
        self.assertEqual(VM(bytecode).run(), [[1, 2], 15])
        recovered = self.request('recover-png', image=image)
        self.assertIn("+=", recovered['files'][recovered['entry']])
        rebuilt = self.request('compile', files=recovered['files'], entry=recovered['entry'])
        self.assertEqual(VM(rebuilt['bytecode']).run(), [[1, 2], 15])

    def test_all_locations_and_operators(self):
        self.assertEqual(
            self.run_code("""record Box { value: int }
effect fn main() {
 var n = 20
 var a: [int] = [20]
 var b: Box = Box{value: 20}
 var m: map[string, int] = map[string, int]{"x": 20}
 n += 4; a[0] += 4; b.value += 4; m["x"] += 4
 n -= 2; a[0] -= 2; b.value -= 2; m["x"] -= 2
 n *= 3; a[0] *= 3; b.value *= 3; m["x"] *= 3
 n /= 2; a[0] /= 2; b.value /= 2; m["x"] /= 2
 n %= 7; a[0] %= 7; b.value %= 7; m["x"] %= 7
 n += 1; a[0] += 1; b.value += 1; m["x"] += 1
 n -= 1; a[0] -= 1; b.value -= 1; m["x"] -= 1
 print(n); print(a[0]); print(b.value); print(m["x"])
 var words: [string] = ["a"]
 words[0] += "b"
 print(words[0])
}"""),
            [5, 5, 5, 5, "ab"],
        )

    def test_index_is_evaluated_once(self):
        self.assertEqual(
            self.run_code("""
fn idx(calls: inout [int]) -> int { calls[0] += 1; return 0 }
fn rhs() -> int = 2
effect fn main() {
 var calls: [int] = [0]
 var a: [int] = [8]
 a[idx(&calls)] *= rhs()
 print(a); print(calls)
}"""),
            [[16], [1]],
        )

    def test_failed_read_does_not_evaluate_rhs(self):
        code = self.request('compile', files={'main.pxl': """
effect fn rhs() -> int { print("rhs was evaluated"); return 2 }
effect fn main() {
 var m = map[string, int]{}
 print(len(m))
 m["missing"] += rhs()
}"""})['bytecode']
        vm = VM(code)
        with self.assertRaisesRegex(PixelError, 'Missing map key'):
            vm.run()
        self.assertEqual(vm.output, [0])

    def test_overlapping_rhs_write_is_rejected(self):
        with self.assertRaises(PixelError):
            self.run_code('fn rhs(a: inout [int]) -> int { a[0] = 100; return 2 } effect fn main() { var a = [8]; a[0] *= rhs(&a) }')

    def test_function_result_is_not_an_assignable_root(self):
        with self.assertRaises(PixelError):
            self.run_code('fn receiver(a: [int]) -> [int] = a; effect fn main() { var a = [1]; receiver(a)[0] += 1 }')

    def test_invalid_updates_are_static_errors(self):
        for statement in [
            "let x = 1; x += 1",
            "var a: [bool] = [true]; a[0] += false",
            'var a: [string] = ["a"]; a[0] -= "b"',
            "var a: [[int]] = [[1]]; a[0] += [2]",
            'match parseInt("bad") { .Ok(value) => {}, .Err(e) => { e.message += "x" } }',
        ]:
            with self.subTest(statement=statement), self.assertRaises(PixelError):
                self.run_code("effect fn main() { " + statement + " }")

    def test_replay_restores_each_intermediate_stack_and_heap(self):
        compiled = self.compiler.debug_project(
            {
                "main.pxl": """effect fn main() {
 var a: [int] = [3]
 a[0] *= 4
 a[0] += 1
 print(a)
}"""
            }
        )
        timeline = Timeline(compiled.source, compiled=compiled)
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
