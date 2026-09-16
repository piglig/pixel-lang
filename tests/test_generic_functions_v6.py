import unittest

from pixellang.compiler import compile_source
from pixellang.editor import SymbolIndex
from pixellang.model import PixelError
from pixellang.picture import decode_picture, encode_picture
from pixellang.project import compile_project, project_text
from pixellang.temporal import Timeline
from pixellang.vm import VM
from pixellang.workstation import Workstation


class GenericFunctionTests(unittest.TestCase):
    def test_distinct_instances_survive_image_and_text(self):
        files = {
            "main.pxl": """fn identity[T](value: T) -> T = value
fn first[T](values: [T]) -> T = values[0]
fn main() {
 print(identity[int](42))
 print(identity[string]("cat"))
 print(first[float64]([1.25, 2.5]))
}"""
        }
        source, compiled = compile_project(files)
        expected = [42, "cat", 1.25]
        self.assertEqual(VM(compiled.bytecode).run(), expected)
        self.assertEqual(len(compiled.bytecode["functions"]), 4)
        for function in compiled.bytecode["functions"].values():
            self.assertNotIn("$", str(function["params"]))
            self.assertNotIn("$", function["result"])
        image = decode_picture(encode_picture(source))
        self.assertEqual(VM(compile_source(image).bytecode).run(), expected)
        recovered = project_text(image)["files"]
        self.assertIn("identity[T]", recovered["main.pxl"])
        self.assertEqual(VM(compile_project(recovered)[1].bytecode).run(), expected)

    def test_generic_records_functions_and_imports_compose(self):
        files = {
            "boxes.pxl": """export record Box[T] { value: T }
export fn box[T](value: T) -> Box[T] = Box[T]{value}
export fn read[T](box: Box[T]) -> T = box.value""",
            "main.pxl": """import "boxes.pxl" as boxes
fn main() {
 let boxed = boxes.box[[int]]([1, 2])
 print(boxes.read[[int]](boxed))
}""",
        }
        source, compiled = compile_project(files)
        self.assertEqual(VM(compiled.bytecode).run(), [[1, 2]])
        self.assertEqual(
            VM(compile_project(project_text(source)["files"])[1].bytecode).run(),
            [[1, 2]],
        )

    def test_recursive_function_and_replay(self):
        source, compiled = compile_project(
            {
                "main.pxl": """fn repeat[T](value: T, count: int) -> [T] {
 if count == 0 { return ([]: [T]) }
 let result = repeat[T](value, count - 1)
 append(result, value)
 return result
}
fn main() { print(repeat[string]("x", 3)) }"""
            }
        )
        self.assertEqual(VM(compiled.bytecode).run(), [["x", "x", "x"]])
        self.assertEqual(len(compiled.bytecode["functions"]), 2)
        timeline = Timeline(source)
        states = [timeline.vm.state()]
        while not timeline.vm.halted:
            timeline.advance()
            states.append(timeline.vm.state())
        restored = Timeline.restore(timeline.document())
        for z in reversed(range(len(states))):
            self.assertEqual(restored.seek(z), states[z])

    def test_invalid_templates_arguments_and_private_calls(self):
        cases = [
            "fn id[T](x: T) -> T = x\nfn main() { id[string](1) }",
            "fn id[T](x: T) -> T = x\nfn main() { id[int, string](1) }",
            "fn id[T](x: T) -> T = missing\nfn main() {}",
            "fn add[T](x: T) -> T = x + x\nfn main() {}",
            "fn eq[T](x: T) -> bool = x == x\nfn main() {}",
            "fn main[T]() {}",
            "fn main() { print(len[int]([1])) }",
            "fn grow[T](x: [T]) { grow[[T]]([x]) }\nfn main() { grow[int]([1]) }",
        ]
        for code in cases:
            with self.subTest(code=code), self.assertRaises(PixelError):
                compile_project({"main.pxl": code})
        with self.assertRaisesRegex(PixelError, "private"):
            compile_project(
                {
                    "private.pxl": "fn id[T](x: T) -> T = x",
                    "main.pxl": 'import "private.pxl"\nfn main() { private.id[int](1) }',
                }
            )

    def test_navigation_rename_and_debug_instances(self):
        code = "fn identity[T](value: T) -> T = value\nfn main() { print(identity[int](42)) }"
        index = SymbolIndex({"main.pxl": code})
        call = index.at("main.pxl", code.rindex("identity"))
        self.assertEqual(
            index.symbols[call["symbol"]]["span"].start, code.index("identity")
        )
        self.assertEqual(
            len(index.rename("main.pxl", code.index("[T]") + 1, "Item")), 3
        )
        work = Workstation()
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

    def test_generic_standard_library_preserves_shallow_copy_semantics(self):
        _, compiled = compile_project(
            {
                "main.pxl": """import "std/collections.pxl" as collections
record Box { value: int }
fn main() {
 let source = [Box{value: 1}]
 let copied = collections.Copy[Box](source)
 copied[0].value = 2
 append(copied, Box{value: 3})
 print(len(source)); print(source[0].value)
 let repeated = collections.Repeat[string]("x", 3)
 print(repeated)
 try { collections.Repeat[int](1, -1) } catch error { print(error.code) }
}"""
            }
        )
        self.assertEqual(
            VM(compiled.bytecode).run(), [1, 2, ["x", "x", "x"], "user.failure"]
        )


if __name__ == "__main__":
    unittest.main()
