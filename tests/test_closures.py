import copy
import unittest

from pixellang.codec import validate
from pixellang.compiler_client import CompilerClient
from pixellang.model import PixelError
from pixellang.native_editor import NativeSymbolIndex
from pixellang.temporal import Timeline
from pixellang.vm import VM, validate_bytecode
from pixellang.workstation import Workstation


class ClosureTests(unittest.TestCase):
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

    def index(self, files):
        return NativeSymbolIndex(files, 'main.pxl', self.request('editor-index', files=files)['index'])

    def execute(self, code):
        return VM(self.compile({"main.pxl": code})).run()

    def test_capture_lifetime_image_recovery_and_every_replay_state(self):
        compiled = self.compiler.debug_project(
            {
                "main.pxl": """fn counter() -> fn() -> int {
 var count = 1
 let read = fn() -> int { return count }
 count = 99
 return read
}
effect fn main() {
 let first = counter()
 let second = counter()
 print(first()); print(first()); print(second()); print(first())
}"""
            }
        )
        expected = [1, 1, 1, 1]
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

    def test_siblings_keep_creation_values_after_outer_assignment(self):
        self.assertEqual(
            self.execute("""effect fn main() {
 var count = 1
 let first = fn() -> int { return count }
 count = 2
 let second = fn() -> int { return count }
 print(first()); print(second()); count = 10; print(first()); print(second())
}"""),
            [1, 2, 1, 2],
        )

    def test_each_loop_entry_has_fresh_bindings(self):
        self.assertEqual(
            self.execute("""effect fn main() {
 var readers: [fn() -> int] = []
 for index, value in [10, 20, 30] {
  var local = value
  readers.append(fn() -> int { return index + local + 1 })
  local = 99
 }
 print(readers[0]()); print(readers[1]()); print(readers[2]()); print(readers[0]())
}"""),
            [11, 22, 33, 11],
        )

    def test_transitive_generic_capture_and_gc(self):
        self.assertEqual(
            self.execute("""fn wrap[T](value: T) -> fn() -> fn() -> T {
 return fn() -> fn() -> T { return fn() -> T { return value } }
}
effect fn main() {
 let read = wrap::[[int]]([42])()
 var index = 0
 while index < 300 { let garbage = [index]; index += 1 }
 print(read()[0])
}"""),
            [42],
        )

    def test_capture_navigation_and_debug_local_values(self):
        code = """effect fn main() {
 var count = 0
 let next = fn() -> int { return count + 1 }
 print(next())
}"""
        index = self.index({"main.pxl": code})
        self.assertEqual(len(index.rename("main.pxl", code.index("count"), "total", analyze=self.index)), 2)
        service = Workstation()
        self.addCleanup(service.close)
        state = service.request("build", {"files": {"main.pxl": code}})
        args = {"session": state["session"]}
        observed = False
        while not state["state"]["halted"]:
            state = service.request("step", args)
            if state["frames"] and state["frames"][0]["display_name"] == "lambda":
                frame = state["frames"][0]
                self.assertIn("count", frame["names"])
                values = service.request(
                    "variables", {**args, "z": state["cursor"], "path": ["frame", 0]}
                )["variables"]
                self.assertIn(values[0]["value"], ["0", "1"])
                observed = True
        self.assertTrue(observed)
        self.assertEqual(state["state"]["output"], [1])

    def test_reassigned_closure_captures_previous_function_and_array_value(self):
        self.assertEqual(
            self.execute("""effect fn main() {
 var factorial: fn(int) -> int = fn(n: int) -> int { return n }
 factorial = fn(n: int) -> int {
  if n < 2 { return 1 }
  return n * factorial(n - 1)
 }
 var values: [int] = [1]
 let read = fn() -> int { return values[0] }
 let update = fn(target: inout [int]) { target[0] += 1 }
 update(&values); print(values[0]); print(read()); print(factorial(5))
}"""),
            # The replacement captures the previous identity function. It
            # does not gain a mutable reference to its own outer binding.
            [2, 1, 20],
        )

    def test_nested_formatter_is_indented_and_idempotent(self):
        code = """effect fn main() {
let outer = fn() -> fn() -> int {
return fn() -> int {
return 42
}
}
print(outer()())
}"""
        formatted = self.request('format', files={'main.pxl': code})['text']
        self.assertIn(
            "        return fn() -> int {\n            return 42\n        }",
            formatted,
        )
        self.assertEqual(self.request('format', files={'main.pxl': formatted})['text'], formatted)
        self.assertEqual(self.execute(formatted), [42])

    def test_closure_environment_names_paging_and_reverse_state(self):
        code = """effect fn main() {
 var count = 0
 let values = [10, 20]
 let next = fn() -> int { return count + values[0] }
 count = 99
 print(next())
 print(next())
}"""
        service = Workstation()
        self.addCleanup(service.close)
        state = service.request("build", {"files": {"main.pxl": code}})
        args = {"session": state["session"]}
        snapshots = []
        while not state["state"]["halted"]:
            if state["frames"] and state["frames"][0]["display_name"] == "main":
                locals_ = service.request(
                    "variables", {**args, "z": state["cursor"], "path": ["frame", 0]}
                )["variables"]
                if len(locals_) > 2 and locals_[2]["path"]:
                    path = locals_[2]["path"]
                    page = service.request(
                        "variables",
                        {
                            **args,
                            "z": state["cursor"],
                            "path": path,
                            "start": 0,
                            "count": 1,
                        },
                    )
                    self.assertEqual(page["total"], 2)
                    self.assertEqual(page["variables"][0]["name"], "count")
                    snapshots.append((state["cursor"], path, page))
            state = service.request("step", args)
        self.assertTrue(snapshots)
        for z, path, expected in reversed(snapshots):
            service.request("seek", {**args, "z": z})
            self.assertEqual(
                service.request(
                    "variables", {**args, "z": z, "path": path, "start": 0, "count": 1}
                ),
                expected,
            )

    def test_missing_or_unreferenced_pixel_lambda_body_rejected(self):
        source = self.request("pixels", files={"main.pxl": "effect fn main() { let f = fn() -> int { return 1 }; print(f()) }"})["document"]
        tokens = validate(source)
        body_row = next(
            t.span.points[0][1]
            for t in tokens
            if t.kind == "keyword" and t.value == "lambda_body"
        )
        reference_row = next(
            t.span.points[0][1]
            for t in tokens
            if t.kind == "keyword" and t.value == "lambda"
        )
        for remove in [lambda row: row >= body_row, lambda row: row == reference_row]:
            damaged = copy.deepcopy(source)
            damaged["pixels"] = [
                p for p in damaged["pixels"] if not remove(p["position"][1])
            ]
            with self.assertRaises(PixelError):
                self.request("compile-pixels", document=damaged)

    def test_malformed_closure_bytecode_is_rejected(self):
        compiled = self.compile(
            {
                "main.pxl": "effect fn main() { let n = 1; let f = fn() -> int { return n }; print(f()) }"
            }
        )
        original = compiled
        target = next(key for key, function in original["functions"].items() if function["captures"])
        for captures in [None, ["unit"], ["not-a-type"], ["int"] * 65537]:
            bc = copy.deepcopy(original)
            bc["functions"][target]["captures"] = captures
            with (
                self.subTest(captures=str(captures)[:40]),
                self.assertRaises(PixelError),
            ):
                validate_bytecode(bc)
        for op in ["CALL", "FUNCTION"]:
            bc = copy.deepcopy(original)
            instructions = bc["functions"][bc["entry"][0]]["instructions"]
            next(i for i in instructions if i["op"] == "CLOSURE")["op"] = op
            with self.subTest(op=op), self.assertRaises(PixelError):
                validate_bytecode(bc)
        bc = copy.deepcopy(original)
        bc["functions"][target] = []
        with self.assertRaises(PixelError):
            validate_bytecode(bc)
        bc = copy.deepcopy(original)
        bc["functions"][bc["entry"][0]]["captures"] = ["int"]
        with self.assertRaises(PixelError):
            validate_bytecode(bc)

    def test_incomplete_capture_completion_includes_visible_outer_bindings(self):
        code = "effect fn main() { let count = 42; let make = fn() { let nested = fn() { cou"
        proposals = self.index({"main.pxl": code}).complete("main.pxl", len(code), analyze=self.index)
        self.assertEqual(
            proposals, [{"label": "count", "kind": "Variable", "detail": "int"}]
        )
        code = "effect fn main() { let callback = fn(count: bool) { cou"
        proposals = self.index({"main.pxl": code}).complete("main.pxl", len(code), analyze=self.index)
        self.assertEqual(
            proposals, [{"label": "count", "kind": "Variable", "detail": "bool"}]
        )

    def test_immutable_capture_and_wrong_return_rejected(self):
        for code in [
            "effect fn main() { let n = 0; let f = fn() { n = 1 } }",
            "effect fn main() { var n = 0; let f = fn() { n = 1 } }",
            "effect fn main() { let f = fn() -> int {} }",
            "effect fn main() { let f = fn() -> int { return true } }",
            "effect fn main() { let f: fn(int) = fn(n: bool) {} }",
        ]:
            with self.subTest(code=code), self.assertRaises(PixelError):
                self.compile({"main.pxl": code})


if __name__ == "__main__":
    unittest.main()
