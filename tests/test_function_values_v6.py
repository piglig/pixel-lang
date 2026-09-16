import unittest

from pixellang.compiler import compile_source
from pixellang.editor import SymbolIndex
from pixellang.model import PixelError
from pixellang.picture import decode_picture, encode_picture
from pixellang.project import compile_project, project_text
from pixellang.temporal import Timeline
from pixellang.vm import VM


class FunctionValueTests(unittest.TestCase):
    def test_callback_return_container_image_and_replay(self):
        code = """fn double(n: int) -> int = n * 2
fn choose() -> fn(int) -> int = double
fn apply(operation: fn(int) -> int, value: int) -> int = operation(value)
fn main() {
 let operations = [choose(), double]
 let operation = operations[0]
 print(operation(21))
 print(apply(operations[1], 3))
}"""
        source, compiled = compile_project({"main.pxl": code})
        expected = [42, 6]
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

    def test_callable_expression_order_and_shared_target(self):
        code = """record Ops { apply: fn(int) -> int }
fn twice(n: int) -> int = n * 2
fn other(n: int) -> int = n + 100
fn choose(ops: Ops) -> fn(int) -> int { print(1); return ops.apply }
fn argument(ops: Ops) -> int { print(2); ops.apply = other; return 21 }
fn main() {
 let ops = Ops{apply: twice}
 print(choose(ops)(argument(ops)))
 print(ops.apply(1))
 print([twice][0](3))
 print((twice)(4))
}"""
        source, compiled = compile_project({"main.pxl": code})
        expected = [1, 2, 42, 101, 6, 8]
        self.assertEqual(VM(compiled.bytecode).run(), expected)
        image = decode_picture(encode_picture(source))
        self.assertEqual(VM(compile_source(image).bytecode).run(), expected)
        self.assertEqual(
            VM(compile_project(project_text(image)["files"])[1].bytecode).run(),
            expected,
        )

    def test_index_call_can_shadow_a_function_name(self):
        code = """fn twice(n: int) -> int = n * 2
fn main() {
 let twice = [twice]
 let index = 0
 print(twice[index](21))
 print(twice[0](3))
}"""
        self.assertEqual(
            VM(compile_project({"main.pxl": code})[1].bytecode).run(), [42, 6]
        )

    def test_callable_target_failure_prevents_argument_effects(self):
        code = """fn twice(n: int) -> int = n * 2
fn argument() -> int { print(999); return 1 }
fn main() {
 let operations: [fn(int) -> int] = []
 try { print(operations[0](argument())) }
 catch error { print(error.code) }
}"""
        result = VM(compile_project({"main.pxl": code})[1].bytecode).run()
        self.assertEqual(len(result), 1)
        self.assertNotIn(999, result)

    def test_unit_callback_and_recoverable_errors(self):
        code = """fn output(n: int) { print(n) }
fn broken(n: int) { fail("callback failed") }
fn apply(operation: fn(int), value: int) { operation(value) }
fn main() {
 apply(output, 42)
 try { apply(broken, 1) } catch error { print(error.message) }
 try { print(jsonFrom(output)) } catch error { print(error.code) }
}"""
        self.assertEqual(
            VM(compile_project({"main.pxl": code})[1].bytecode).run(),
            [42, "callback failed", "json.unsupported_type"],
        )

    def test_bad_signatures_calls_equality_and_json(self):
        for body in [
            "let f: fn(bool) -> int = double",
            "let f = double; print(f(true))",
            "let f = double; print(f())",
            "let f = double; print(f[int](1))",
            "let f = 1; print(f(2))",
            "print(double == double)",
            'let f = jsonDecode[fn(int) -> int]("{}")',
        ]:
            with self.subTest(body=body), self.assertRaises(PixelError):
                compile_project(
                    {
                        "main.pxl": "fn double(n: int) -> int = n * 2\nfn main() { "
                        + body
                        + " }"
                    }
                )

    def test_specialized_module_references_and_private_caller_type(self):
        files = {
            "library.pxl": "export fn identity[T](value: T) -> T = value",
            "main.pxl": """import "library.pxl" as library
record Row { value: int }
fn specialized[T]() -> fn(T) -> T = library.identity[T]
fn main() {
 let transform = specialized[Row]()
 print(transform(Row{value: 42}).value)
 let scalar = library.identity[int]
 print(scalar(3))
}""",
        }
        source, compiled = compile_project(files)
        self.assertEqual(VM(compiled.bytecode).run(), [42, 3])
        image = decode_picture(encode_picture(source))
        self.assertEqual(VM(compile_source(image).bytecode).run(), [42, 3])
        self.assertEqual(
            VM(compile_project(project_text(image)["files"])[1].bytecode).run(), [42, 3]
        )
        index = SymbolIndex(files)
        self.assertEqual(
            len(
                index.rename(
                    "library.pxl", files["library.pxl"].index("identity"), "same"
                )
            ),
            3,
        )

    def test_local_binding_shadows_module_for_field_reference_and_call(self):
        files = {
            "library.pxl": "export fn apply(n: int) -> int = n + 100",
            "main.pxl": """import "library.pxl" as library
record Ops { apply: fn(int) -> int }
fn twice(n: int) -> int = n * 2
fn main() {
 print(library.apply(1))
 let library = Ops{apply: twice}
 let operation = library.apply
 print(operation(3))
 print(library.apply(4))
}""",
        }
        source, compiled = compile_project(files)
        self.assertEqual(VM(compiled.bytecode).run(), [101, 6, 8])
        self.assertEqual(
            VM(compile_source(decode_picture(encode_picture(source))).bytecode).run(),
            [101, 6, 8],
        )
        index = SymbolIndex(files)
        self.assertEqual(
            len(
                index.rename(
                    "main.pxl", files["main.pxl"].index("let library") + 4, "operations"
                )
            ),
            3,
        )

    def test_reference_privacy_and_specialization_arity(self):
        for expression in [
            "library.hidden",
            "library.identity",
            "library.identity[int, bool]",
            "library.public[int]",
        ]:
            with self.subTest(expression=expression), self.assertRaises(PixelError):
                compile_project(
                    {
                        "main.pxl": 'import "library.pxl" as library\nfn main() { let value = '
                        + expression
                        + " }",
                        "library.pxl": "fn hidden() {}\nexport fn public() {}\nexport fn identity[T](value: T) -> T = value",
                    }
                )

    def test_named_value_and_callback_navigation(self):
        code = "fn double(n: int) -> int = n * 2\nfn main() { let operation = double; print(operation(21)) }"
        index = SymbolIndex({"main.pxl": code})
        self.assertEqual(
            len(index.rename("main.pxl", code.index("double"), "twice")), 2
        )
        self.assertEqual(
            len(index.rename("main.pxl", code.index("operation"), "callback")), 2
        )


if __name__ == "__main__":
    unittest.main()
