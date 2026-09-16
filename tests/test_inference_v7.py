import unittest

from pixellang.compiler import compile_source
from pixellang.model import PixelError
from pixellang.picture import decode_picture, encode_picture
from pixellang.project import compile_project, project_text
from pixellang.temporal import Timeline
from pixellang.vm import VM


class InferenceTests(unittest.TestCase):
    def test_compositional_cross_module_inference_and_source_free_execution(self):
        files = {
            "lib.pxl": """export fn identity[T](value: T) -> T = value
export fn apply[T, U](value: T, f: fn(T) -> U) -> U = f(value)
export fn first[T](values: [T]) -> T = values[0]
export fn read[T](values: map[T]) -> T = values["key"]
export fn option[T](value: Option[T], fallback: T) -> T {
 return match value { Some(x) => x, None => fallback }
}""",
            "main.pxl": """import "lib.pxl" as lib
record Row { amount: int }
fn main() {
 let row = lib.identity(Row{amount: 7})
 print(lib.apply(row, fn(r: Row) -> string { return string(r.amount) }))
 print(lib.first([[1, 2], [3]]))
 print(lib.read(map[float64]{"key": 1.25}))
 print(lib.option(Some(9), 0))
}""",
        }
        source, compiled = compile_project(files)
        expected = ["7", [1, 2], 1.25, 9]
        self.assertEqual(VM(compiled.bytecode).run(), expected)
        image = decode_picture(encode_picture(source))
        self.assertEqual(VM(compile_source(image).bytecode).run(), expected)
        recovered = project_text(image)["files"]
        self.assertEqual(VM(compile_project(recovered)[1].bytecode).run(), expected)

    def test_deferred_empty_arguments_and_abstract_recursion(self):
        code = """fn choose[T](values: [T], fallback: T) -> T {
 if len(values) == 0 { return fallback }
 return choose(([]: [T]), values[0])
}
fn missing[T](value: Option[T], fallback: T) -> T {
 return match value { Some(x) => x, None => fallback }
}
fn main() {
 print(choose([], "fallback"))
 print(choose([42], 0))
 print(missing(None, 3))
}"""
        self.assertEqual(
            VM(compile_project({"main.pxl": code})[1].bytecode).run(),
            ["fallback", 42, 3],
        )

    def test_conflicts_and_missing_evidence_are_rejected(self):
        cases = [
            ("fn same[T](a: T, b: T) {}\nfn main() { same(1, 1.0) }", "Conflicting"),
            (
                "fn first[T](a: [T]) -> T = a[0]\nfn main() { first([]) }",
                "Cannot infer",
            ),
            ("fn make[T]() -> [T] = ([]: [T])\nfn main() { make() }", "explicit type"),
            ("fn first[T](a: [T]) -> T = a[0]\nfn main() { first(1) }", "Cannot infer"),
        ]
        for code, message in cases:
            with self.subTest(code=code), self.assertRaisesRegex(PixelError, message):
                compile_project({"main.pxl": code})

    def test_argument_effects_run_once_in_order_and_replay(self):
        code = """fn same[T](a: T, b: T) -> T = b
fn main() {
 var count = 0
 let next = fn() -> int { count += 1; print(count); return count }
 print(same(next(), next()))
 print(count)
}"""
        source, compiled = compile_project({"main.pxl": code})
        self.assertEqual(VM(compiled.bytecode).run(), [1, 2, 2, 2])
        timeline = Timeline(source)
        states = [timeline.vm.state()]
        while not timeline.vm.halted:
            timeline.advance()
            states.append(timeline.vm.state())
        restored = Timeline.restore(timeline.document())
        for z in reversed(range(len(states))):
            self.assertEqual(restored.seek(z), states[z])
