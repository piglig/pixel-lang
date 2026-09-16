import copy
import unittest

from pixellang.codec import validate
from pixellang.compiler import compile_source
from pixellang.editor import SymbolIndex, complete
from pixellang.model import PixelError
from pixellang.picture import decode_picture, encode_picture
from pixellang.project import compile_project, project_text
from pixellang.temporal import Timeline
from pixellang.vm import VM, validate_bytecode
from pixellang.workstation import Workstation


class ClosureTests(unittest.TestCase):
    def execute(self, code):
        return VM(compile_project({"main.pxl": code})[1].bytecode).run()

    def test_counter_lifetime_image_recovery_and_every_replay_state(self):
        source, compiled = compile_project(
            {
                "main.pxl": """fn counter() -> fn() -> int {
 var count = 0
 return fn() -> int { count += 1; return count }
}
fn main() {
 let first = counter()
 let second = counter()
 print(first()); print(first()); print(second()); print(first())
}"""
            }
        )
        expected = [1, 2, 1, 3]
        self.assertEqual(VM(compiled.bytecode).run(), expected)
        image = decode_picture(encode_picture(source))
        self.assertEqual(VM(compile_source(image).bytecode).run(), expected)
        self.assertEqual(
            VM(compile_project(project_text(image)["files"])[1].bytecode).run(),
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

    def test_siblings_share_outer_assignment(self):
        self.assertEqual(
            self.execute("""fn main() {
 var count = 1
 let increase = fn() { count += 1 }
 let read = fn() -> int { return count }
 increase(); print(read()); count = 10; print(read())
}"""),
            [2, 10],
        )

    def test_each_loop_entry_has_fresh_bindings(self):
        self.assertEqual(
            self.execute("""fn main() {
 let readers: [fn() -> int] = []
 for index, value in [10, 20, 30] {
  var local = value
  append(readers, fn() -> int { local += 1; return index + local })
 }
 print(readers[0]()); print(readers[1]()); print(readers[2]()); print(readers[0]())
}"""),
            [11, 22, 33, 12],
        )

    def test_transitive_generic_capture_and_gc(self):
        self.assertEqual(
            self.execute("""fn wrap[T](value: T) -> fn() -> fn() -> T {
 return fn() -> fn() -> T { return fn() -> T { return value } }
}
fn main() {
 let read = wrap[[int]]([42])()
 var index = 0
 while index < 300 { let garbage = [index]; index += 1 }
 print(read()[0])
}"""),
            [42],
        )

    def test_capture_navigation_and_debug_local_values(self):
        code = """fn main() {
 var count = 0
 let next = fn() -> int { count += 1; return count }
 print(next())
}"""
        index = SymbolIndex({"main.pxl": code})
        self.assertEqual(len(index.rename("main.pxl", code.index("count"), "total")), 3)
        service = Workstation()
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

    def test_recursive_closure_cell_cycle_and_object_mutation(self):
        self.assertEqual(
            self.execute("""fn main() {
 var factorial: fn(int) -> int = fn(n: int) -> int { return n }
 factorial = fn(n: int) -> int {
  if n < 2 { return 1 }
  return n * factorial(n - 1)
 }
 let values = [1]
 let update = fn() { values[0] += 1 }
 update(); print(values[0]); print(factorial(5))
}"""),
            [2, 120],
        )

    def test_nested_formatter_is_indented_and_idempotent(self):
        source, _ = compile_project(
            {
                "main.pxl": "fn main() { let outer = fn() -> fn() -> int { return fn() -> int { return 42 } }; print(outer()()) }"
            }
        )
        files = project_text(source)["files"]
        self.assertIn(
            "        return fn() -> int {\n            return 42\n        }",
            files["main.pxl"],
        )
        self.assertEqual(project_text(compile_project(files)[0])["files"], files)
        self.assertEqual(VM(compile_project(files)[1].bytecode).run(), [42])

    def test_closure_environment_names_paging_and_reverse_state(self):
        code = """fn main() {
 var count = 0
 let values = [10, 20]
 let next = fn() -> int { count += 1; return count + values[0] }
 print(next())
 print(next())
}"""
        service = Workstation()
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
        source, _ = compile_project(
            {"main.pxl": "fn main() { let f = fn() -> int { return 1 }; print(f()) }"}
        )
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
                compile_source(damaged)

    def test_malformed_closure_bytecode_is_rejected(self):
        _, compiled = compile_project(
            {
                "main.pxl": "fn main() { let n = 1; let f = fn() -> int { return n }; print(f()) }"
            }
        )
        original = compiled.bytecode
        target = next(key for key in original["functions"] if "#" in key)
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
        code = "fn main() { let count = 42; let make = fn() { let nested = fn() { cou"
        proposals = complete({"main.pxl": code}, "main.pxl", "main.pxl", len(code))
        self.assertEqual(
            proposals, [{"label": "count", "kind": "Variable", "detail": "int"}]
        )
        code = "fn main() { let callback = fn(count: bool) { cou"
        proposals = complete({"main.pxl": code}, "main.pxl", "main.pxl", len(code))
        self.assertEqual(
            proposals, [{"label": "count", "kind": "Variable", "detail": "bool"}]
        )

    def test_immutable_capture_and_wrong_return_rejected(self):
        for code in [
            "fn main() { let n = 0; let f = fn() { n = 1 } }",
            "fn main() { let f = fn() -> int {} }",
            "fn main() { let f = fn() -> int { return true } }",
            "fn main() { let f: fn(int) = fn(n: bool) {} }",
        ]:
            with self.subTest(code=code), self.assertRaises(PixelError):
                compile_project({"main.pxl": code})


if __name__ == "__main__":
    unittest.main()
