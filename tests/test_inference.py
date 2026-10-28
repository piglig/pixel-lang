import unittest

from pixellang.model import PixelError
from pixellang.compiler_client import CompilerClient
from pixellang.temporal import Timeline
from pixellang.vm import VM


class InferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def request(self, operation, **args):
        response = self.compiler.request(operation, **args)
        self.assertEqual(response['status'], 'ok', response)
        return response['result']

    def compile(self, files):
        return self.request('compile', files=files)['bytecode']

    def test_compositional_cross_module_inference_and_source_free_execution(self):
        files = {
            "lib.pxl": """export fn identity[T](value: T) -> T = value
export fn apply[T, U](value: T, f: fn(T) -> U) -> U = f(value)
export fn first[T](values: [T]) -> T = values[0]
export fn read[T](values: map[string, T]) -> T = values["key"]
export fn option[T](value: Option[T], fallback: T) -> T {
 return match value { Option.Some(x) => x, Option.None => fallback }
}""",
            "main.pxl": """import "lib.pxl" as lib
record Row { amount: int }
effect fn main() {
 let row = lib.identity(Row{amount: 7})
 print(lib.apply(row, fn(r: Row) -> string { return string(r.amount) }))
 print(lib.first([[1, 2], [3]]))
 print(lib.read(map[string, float64]{"key": 1.25}))
 print(lib.option(Option.Some(9), 0))
}""",
        }
        compiled = self.compile(files)
        expected = ["7", [1, 2], 1.25, 9]
        self.assertEqual(VM(compiled).run(), expected)
        image = self.request('export-png', files=files)['image']
        self.assertEqual(VM(self.request('compile-png', image=image)['bytecode']).run(), expected)
        recovered = self.request('recover-png', image=image)
        self.assertEqual(VM(self.request('compile', **recovered)['bytecode']).run(), expected)

    def test_deferred_empty_arguments_and_abstract_recursion(self):
        code = """fn choose[T](values: [T], fallback: T) -> T {
 if len(values) == 0 { return fallback }
 return choose::[T](([]: [T]), values[0])
}
fn missing[T](value: Option[T], fallback: T) -> T {
 return match value { Option.Some(x) => x, Option.None => fallback }
}
effect fn main() {
 print(choose([], "fallback"))
 print(choose([42], 0))
 print(missing(Option.None, 3))
}"""
        self.assertEqual(
            VM(self.compile({"main.pxl": code})).run(),
            ["fallback", 42, 3],
        )

    def test_conflicts_and_missing_evidence_are_rejected(self):
        cases = [
            ("fn same[T](a: T, b: T) {}\neffect fn main() { same(1, 1.0) }", "Conflicting"),
            (
                "fn first[T](a: [T]) -> T = a[0]\neffect fn main() { first([]) }",
                "Cannot infer",
            ),
            ("fn make[T]() -> [T] = ([]: [T])\neffect fn main() { make() }", "Cannot infer"),
            ("fn first[T](a: [T]) -> T = a[0]\neffect fn main() { first(1) }", "Type shapes do not match"),
        ]
        for code, message in cases:
            with self.subTest(code=code), self.assertRaisesRegex(PixelError, message):
                self.compile({"main.pxl": code})

    def test_argument_effects_run_once_in_order_and_replay(self):
        code = """effect fn next(count: inout int) -> int { count += 1; print(count); return count }
fn same[T](a: T, b: T) -> T = b
effect fn main() {
 var count = 0
 print(same(next(&count), next(&count)))
 print(count)
}"""
        compiled = self.compiler.debug_project({"main.pxl": code})
        self.assertEqual(VM(compiled.bytecode).run(), [1, 2, 2, 2])
        timeline = Timeline(compiled.source, compiled=compiled)
        states = [timeline.vm.state()]
        while not timeline.vm.halted:
            timeline.advance()
            states.append(timeline.vm.state())
        document = timeline.document()
        snapshot = document['source_project']
        replayed = self.compiler.debug_project(snapshot['files'], snapshot['entry'])
        restored = Timeline.restore(document, compiled=replayed)
        for z in reversed(range(len(states))):
            self.assertEqual(restored.seek(z), states[z])
