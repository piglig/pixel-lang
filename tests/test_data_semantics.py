import unittest

from pixellang.compiler_client import CompilerClient
from pixellang.model import PixelError
from pixellang.temporal import Timeline
from pixellang.vm import VM
from pixellang.workstation import Workstation


class DataSemanticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def compile(self, files):
        response = self.compiler.request('compile', files=files)
        self.assertEqual(response['status'], 'ok', response)
        return response['result']['bytecode']

    def test_value_copy_and_explicit_access_preserve_binding_independence(self):
        code = """import "std/collections.pxl" as collections
record Item { amount: int }
fn change(item: inout Item) { item.amount = 9 }
effect fn main() {
 let original = [Item{amount: 1}]
 var copied = original
 change(&copied[0])
 print(copied[0].amount)
 print(original[0].amount)
 copied[0] = Item{amount: 2}
 print(original[0].amount)
 var independent: Item = Item{amount: original[0].amount}
 change(&independent)
 independent.amount = 30
 print(original[0].amount)
 var alias = original
 alias = [Item{amount: 40}]
 print(original[0].amount)
 print(alias[0].amount)
}"""
        compiled = self.compiler.debug_project({"main.pxl": code})
        expected = [9, 1, 1, 1, 1, 40]
        self.assertEqual(VM(compiled.bytecode).run(), expected)
        image = self.compiler.request('export-png', **compiled.source_project)['result']['image']
        image_code = self.compiler.request('compile-png', image=image)['result']['bytecode']
        self.assertEqual(VM(image_code).run(), expected)
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

    def test_binding_and_equality_rejections(self):
        invalid = [
            "effect fn main() { let x = 1; x = 2 }",
            "fn f(x: int) { x = 2 } effect fn main() { f(1) }",
            'effect fn main() { var x = 1; x = "two" }',
            "effect fn main() { print(1 == 1.0) }",
            "effect fn main() { let f = fn() {}; print(f == f) }",
            "effect fn main() { let values = [1]; values[0] = 2 }",
            "record R { x: int } effect fn main() { let value = R{x:1}; value.x = 2 }",
        ]
        for code in invalid:
            with self.subTest(code=code), self.assertRaises(PixelError):
                self.compile({"main.pxl": code})

    def test_immutable_structural_equality(self):
        code = """record R { x: int }
effect fn main() {
 print([1] == [1]); print([1] == [2])
 print(R{x:1} == R{x:1}); print(R{x:1} == R{x:2})
 print(Option.Some(1) == Option.Some(1)); print(Option.Some(1) == Option.Some(2))
}"""
        self.assertEqual(VM(self.compile({'main.pxl': code})).run(), [True, False] * 3)

    def test_partial_mutation_survives_recoverable_failure(self):
        code = """fn update(values: inout [int]) -> Result[bool, string] {
 values[0] = 7
 return .Err("invalid next item")
}
effect fn main() {
 var values: [int] = [1]
 print(match update(&values) { .Ok(value) => "ok", .Err(error) => error })
 print(values[0])
 print(2 == 2); print("a" == "a"); print(true == false)
}"""
        self.assertEqual(
            VM(self.compile({"main.pxl": code})).run(),
            ["invalid next item", 7, True, True, False],
        )

    def test_debugger_observes_independent_copies_at_historical_time(self):
        service = Workstation()
        self.addCleanup(service.close)
        state = service.request(
            "build",
            {
                "files": {
                    "main.pxl": """effect fn main() {
 let original: [int] = [1]
 var alias = original
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
            # Inspect source bindings, excluding compiler-generated temporaries.
            names = state["frames"][0]["names"]
            values = [values[names.index(name)] for name in ("original", "alias")]
            if all(v.get("path") for v in values):
                children = [
                    service.request("variables", {**args, "path": v["path"]})[
                        "variables"
                    ]
                    for v in values
                ]
                if all(child for child in children):
                    observed = [child[0]["value"] for child in children]
                    self.assertEqual(observed[0], "1")
                    snapshots.append(
                        (state["cursor"], [v["path"] for v in values], observed)
                    )
            state = service.request("step", args)
        self.assertTrue(any(s[2] == ["1", "1"] for s in snapshots))
        self.assertTrue(any(s[2] == ["1", "7"] for s in snapshots))
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
