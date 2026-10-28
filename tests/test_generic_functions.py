import unittest

from pixellang.compiler_client import CompilerClient
from pixellang.native_editor import NativeSymbolIndex
from pixellang.model import PixelError
from pixellang.temporal import Timeline
from pixellang.vm import VM
from pixellang.workstation import Workstation


class GenericFunctionTests(unittest.TestCase):
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

    def test_distinct_instances_survive_image_and_text(self):
        files = {
            "main.pxl": """fn identity[T](value: T) -> T = value
fn first[T](values: [T]) -> T = values[0]
effect fn main() {
 print(identity::[int](42))
 print(identity::[string]("cat"))
 print(first::[float64]([1.25, 2.5]))
}"""
        }
        compiled = self.request("compile", files=files)["bytecode"]
        expected = [42, "cat", 1.25]
        self.assertEqual(VM(compiled).run(), expected)
        self.assertEqual(len(compiled["functions"]), 4)
        for function in compiled["functions"].values():
            self.assertNotIn("$", str(function["params"]))
            self.assertNotIn("$", function["result"])
        image = self.request('export-png', files=files)['image']
        self.assertEqual(VM(self.request('compile-png', image=image)['bytecode']).run(), expected)
        recovered = self.request('recover-png', image=image)
        self.assertEqual(VM(self.request('compile', **recovered)['bytecode']).run(), expected)

    def test_generic_records_functions_and_imports_compose(self):
        files = {
            "boxes.pxl": """export record Box[T] { value: T }
export fn box[T](value: T) -> Box[T] = Box::[T]{value: value}
export fn read[T](box: Box[T]) -> T = box.value""",
            "main.pxl": """import "boxes.pxl" as boxes
effect fn main() {
 let boxed = boxes.box::[[int]]([1, 2])
 print(boxes.read::[[int]](boxed))
}""",
        }
        compiled = self.request("compile", files=files)["bytecode"]
        self.assertEqual(VM(compiled).run(), [[1, 2]])
        source = self.request('pixels', files=files)['document']
        recovered = self.request('recover-pixels', document=source)
        self.assertEqual(VM(self.request('compile', **recovered)['bytecode']).run(), [[1, 2]])

    def test_recursive_function_and_replay(self):
        compiled = self.compiler.debug_project(
            {
                "main.pxl": """fn repeat[T](value: T, count: int) -> [T] {
 if count == 0 { return ([]: [T]) }
 var result = repeat::[T](value, count - 1)
 result.append(value)
 return result
}
effect fn main() { print(repeat::[string]("x", 3)) }"""
            }
        )
        self.assertEqual(VM(compiled.bytecode).run(), [["x", "x", "x"]])
        self.assertEqual(len(compiled.bytecode["functions"]), 2)
        timeline = Timeline(compiled.source, compiled=compiled)
        states = [timeline.vm.state()]
        while not timeline.vm.halted:
            timeline.advance()
            states.append(timeline.vm.state())
        restored = Timeline.restore(timeline.document())
        for z in reversed(range(len(states))):
            self.assertEqual(restored.seek(z), states[z])

    def test_invalid_templates_arguments_and_private_calls(self):
        cases = [
            "fn id[T](x: T) -> T = x\neffect fn main() { id::[string](1) }",
            "fn id[T](x: T) -> T = x\neffect fn main() { id::[int, string](1) }",
            "fn id[T](x: T) -> T = missing\neffect fn main() {}",
            "fn add[T](x: T) -> T = x + x\neffect fn main() {}",
            "fn eq[T](x: T) -> bool = x == x\neffect fn main() {}",
            "fn main[T]() {}",
            "effect fn main() { print(len::[int]([1])) }",
            "fn grow[T](x: [T]) { grow::[[T]]([x]) }\neffect fn main() { grow::[int]([1]) }",
        ]
        for code in cases:
            with self.subTest(code=code), self.assertRaises(PixelError):
                self.request("compile", files={"main.pxl": code})
        with self.assertRaisesRegex(PixelError, "private"):
            self.request("compile", files={
                    "private.pxl": "fn id[T](x: T) -> T = x",
                    "main.pxl": 'import "private.pxl"\neffect fn main() { private.id::[int](1) }',
                }
            )

    def test_navigation_rename_and_debug_instances(self):
        code = "fn identity[T](value: T) -> T = value\neffect fn main() { print(identity::[int](42)) }"
        index = self.index({"main.pxl": code})
        call = index.at("main.pxl", code.rindex("identity"))
        self.assertEqual(
            index.symbols[call["symbol"]]["span"]["start"]["offset"], code.index("identity")
        )
        self.assertEqual(
            len(index.rename("main.pxl", code.index("[T]") + 1, "Item", analyze=self.index)), 3
        )
        work = Workstation()
        self.addCleanup(work.close)
        built = work.request("build", {"files": {"main.pxl": code}})
        state, found = built, False
        while not state["state"]["halted"]:
            state = work.request("step", {"session": built["session"]})
            for frame in state["frames"]:
                if frame["display_name"] == "identity[int]":
                    found = True
                    self.assertIn("value", frame["names"])
                    self.assertEqual(frame["display_name"], "identity[int]")
                    self.assertIn(42, frame["memory"])
        self.assertTrue(found)
        self.assertEqual(state["state"]["output"], [42])

    def test_value_assignment_and_generic_recoverable_repeat(self):
        compiled = self.request("compile", files=
            {
                "main.pxl": """import "std/collections.pxl" as collections
record Box { value: int }
effect fn main() {
 let source = [Box{value: 1}]
 var copied = source
 copied[0].value = 2
 copied.append(Box{value: 3})
 print(len(source)); print(source[0].value); print(copied[0].value); print(len(copied))
 let repeated = match collections.Repeat::[string]("x", 3) { .Ok(value) => value, .Err(error) => [] }
 print(repeated)
 match collections.Repeat::[int](1, -1) { .Ok(value) => { print(value) }, .Err(error) => { print(error.code) } }
}"""
            }
        )
        self.assertEqual(
            VM(compiled["bytecode"]).run(), [1, 1, 2, 2, ["x", "x", "x"], "collection.negative_count"]
        )


if __name__ == "__main__":
    unittest.main()
