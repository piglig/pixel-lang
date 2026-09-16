import unittest

from pixellang.compiler import compile_source
from pixellang.model import PixelError
from pixellang.picture import decode_picture, encode_picture
from pixellang.project import compile_project, project_text
from pixellang.temporal import Timeline
from pixellang.vm import VM
from pixellang.workstation import Workstation


class DataSemanticsTests(unittest.TestCase):
    def test_shared_arguments_shallow_copy_and_explicit_independence(self):
        code = """import "std/collections.pxl" as collections
record Item { amount: int }
fn change(item: Item) { item.amount = 9 }
fn main() {
 let original = [Item{amount: 1}]
 let copied = collections.Copy(original)
 change(copied[0])
 print(original[0].amount)
 copied[0] = Item{amount: 2}
 print(original[0].amount)
 let independent = Item{amount: original[0].amount}
 change(independent)
 independent.amount = 30
 print(original[0].amount)
 var alias = original
 alias = [Item{amount: 40}]
 print(original[0].amount)
 print(alias[0].amount)
}"""
        source, compiled = compile_project({"main.pxl": code})
        expected = [9, 9, 9, 9, 40]
        self.assertEqual(VM(compiled.bytecode).run(), expected)
        image = decode_picture(encode_picture(source))
        self.assertEqual(VM(compile_source(image).bytecode).run(), expected)
        recovered = project_text(image)
        self.assertEqual(
            VM(
                compile_project(recovered["files"], recovered["entry"])[1].bytecode
            ).run(),
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

    def test_binding_and_equality_rejections(self):
        invalid = [
            "fn main() { let x = 1; x = 2 }",
            "fn f(x: int) { x = 2 } fn main() { f(1) }",
            'fn main() { var x = 1; x = "two" }',
            "fn main() { print([1] == [1]) }",
            "record R { x: int } fn main() { print(R{x: 1} == R{x: 1}) }",
            "fn main() { print(1 == 1.0) }",
            "fn main() { print(Some(1) == Some(1)) }",
        ]
        for code in invalid:
            with self.subTest(code=code), self.assertRaises(PixelError):
                compile_project({"main.pxl": code})

    def test_partial_mutation_survives_recoverable_failure(self):
        code = """fn update(values: [int]) {
 values[0] = 7
 fail("invalid next item")
}
fn main() {
 let values = [1]
 try { update(values) } catch error { print(error.code) }
 print(values[0])
 print(2 == 2); print("a" == "a"); print(true == false)
}"""
        self.assertEqual(
            VM(compile_project({"main.pxl": code})[1].bytecode).run(),
            ["user.failure", 7, True, True, False],
        )

    def test_debugger_observes_both_aliases_at_historical_time(self):
        service = Workstation()
        state = service.request(
            "build",
            {
                "files": {
                    "main.pxl": """fn main() {
 let original = [1]
 let alias = original
 alias[0] = 7
 print(original[0])
}"""
                }
            },
        )
        args = {"session": state["session"]}
        snapshots = []
        while not state["state"]["halted"]:
            args["z"] = state["cursor"]
            values = service.request("variables", {**args, "path": ["frame", 0]})[
                "variables"
            ]
            if len(values) == 2 and all(v.get("path") for v in values):
                children = [
                    service.request("variables", {**args, "path": v["path"]})[
                        "variables"
                    ]
                    for v in values
                ]
                if all(child for child in children):
                    observed = [child[0]["value"] for child in children]
                    self.assertEqual(observed[0], observed[1])
                    snapshots.append(
                        (state["cursor"], [v["path"] for v in values], observed)
                    )
            state = service.request("step", args)
        self.assertTrue(any(s[2] == ["1", "1"] for s in snapshots))
        self.assertTrue(any(s[2] == ["7", "7"] for s in snapshots))
        for z, paths, expected in reversed(snapshots):
            args["z"] = z
            service.request("seek", {**args, "z": z})
            self.assertEqual(
                [
                    service.request("variables", {**args, "path": p})["variables"][0][
                        "value"
                    ]
                    for p in paths
                ],
                expected,
            )
