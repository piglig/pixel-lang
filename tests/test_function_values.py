import unittest

from pixellang.compiler_client import CompilerClient
from pixellang.model import PixelError
from pixellang.native_editor import NativeSymbolIndex
from pixellang.temporal import Timeline
from pixellang.vm import VM


class FunctionValueTests(unittest.TestCase):
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

    def roundtrip(self, files, expected):
        self.assertEqual(VM(self.compile(files)).run(), expected)
        image = self.request('export-png', files=files)['image']
        self.assertEqual(VM(self.request('compile-png', image=image)['bytecode']).run(), expected)
        recovered = self.request('recover-png', image=image)
        self.assertEqual(VM(self.request('compile', **recovered)['bytecode']).run(), expected)

    def test_callback_return_container_image_and_replay(self):
        code = """fn double(n: int) -> int = n * 2
fn choose() -> fn(int) -> int = double
fn apply(operation: fn(int) -> int, value: int) -> int = operation(value)
effect fn main() {
 let operations = [choose(), double]
 let operation = operations[0]
 print(operation(21))
 print(apply(operations[1], 3))
}"""
        compiled = self.compiler.debug_project({"main.pxl": code})
        expected = [42, 6]
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

    def test_callable_expression_order_and_shared_target(self):
        code = """record Ops { apply: fn(int) -> int }
fn twice(n: int) -> int = n * 2
fn other(n: int) -> int = n + 100
effect fn choose(ops: Ops) -> fn(int) -> int { print(1); return ops.apply }
effect fn argument(ops: inout Ops) -> int { print(2); ops.apply = other; return 21 }
effect fn main() {
 var ops: Ops = Ops{apply: twice}
 print(choose(ops)(argument(&ops)))
 print(ops.apply(1))
 print([twice][0](3))
 print((twice)(4))
}"""
        self.roundtrip({"main.pxl": code}, [1, 2, 42, 101, 6, 8])

    def test_index_call_can_shadow_a_function_name(self):
        code = """fn twice(n: int) -> int = n * 2
effect fn main() {
 let twice = [twice]
 let index = 0
 print(twice[index](21))
 print(twice[0](3))
}"""
        self.assertEqual(
            VM(self.compile({"main.pxl": code})).run(), [42, 6]
        )

    def test_callable_target_failure_prevents_argument_effects(self):
        code = """fn twice(n: int) -> int = n * 2
effect fn argument() -> int { print(999); return 1 }
effect fn main() {
 let operations: [fn(int) -> int] = []
 print(operations[0](argument()))
}"""
        vm = VM(self.compile({"main.pxl": code}))
        with self.assertRaises(PixelError):
            vm.run()
        self.assertEqual(vm.output, [])

    def test_unit_callback_and_recoverable_errors(self):
        code = """effect fn output(n: int) { print(n) }
fn broken(n: int)->Result[int,Error] = .Err(Error{kind:"user",code:"user.callback",message:"callback failed",path:"",operation:"callback",expected:"",actual:""})
effect fn apply(operation: effect fn(int), value: int) { operation(value) }
fn applyResult(operation: fn(int)->Result[int,Error], value:int)->Result[int,Error] = operation(value)
effect fn main() {
 apply(output, 42)
 match applyResult(broken,1) { .Ok(value)=>{print(value)}, .Err(error)=>{print(error.message)} }
}"""
        self.assertEqual(VM(self.compile({"main.pxl": code})).run(), [42, "callback failed"])

    def test_callable_json_encoding_is_rejected_at_runtime(self):
        code='fn double(n:int)->int=n*2\neffect fn main(){print(jsonFrom(double))}'
        vm=VM(self.compile({'main.pxl':code}))
        with self.assertRaises(PixelError) as caught:
            vm.run()
        self.assertEqual(caught.exception.code,'json.unsupported_type')
        self.assertEqual(vm.output,[])

    def test_bad_signatures_calls_equality_and_json(self):
        for body in [
            "let f: fn(bool) -> int = double",
            "let f = double; print(f(true))",
            "let f = double; print(f())",
            "let f = double; print(f::[int](1))",
            "let f = 1; print(f(2))",
            "print(double == double)",
            'let f = jsonDecode::[fn(int) -> int]("{}")',
        ]:
            with self.subTest(body=body), self.assertRaises(PixelError):
                self.compile(
                    {
                        "main.pxl": "fn double(n: int) -> int = n * 2\neffect fn main() { "
                        + body
                        + " }"
                    }
                )

    def test_specialized_module_references_and_private_caller_type(self):
        files = {
            "library.pxl": "export fn identity[T](value: T) -> T = value",
            "main.pxl": """import "library.pxl" as library
record Row { value: int }
fn specialized[T]() -> fn(T) -> T = library.identity::[T]
effect fn main() {
 let transform = specialized::[Row]()
 print(transform(Row{value: 42}).value)
 let scalar = library.identity::[int]
 print(scalar(3))
}""",
        }
        self.roundtrip(files, [42, 3])
        index = self.index(files)
        self.assertEqual(
            len(
                index.rename(
                    "library.pxl", files["library.pxl"].index("identity"), "same", analyze=self.index
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
effect fn main() {
 print(library.apply(1))
 let library = Ops{apply: twice}
 let operation = library.apply
 print(operation(3))
 print(library.apply(4))
}""",
        }
        self.roundtrip(files, [101, 6, 8])
        index = self.index(files)
        self.assertEqual(
            len(
                index.rename(
                    "main.pxl", files["main.pxl"].index("let library") + 4, "operations", analyze=self.index
                )
            ),
            3,
        )

    def test_reference_privacy_and_specialization_arity(self):
        for expression in [
            "library.hidden",
            "library.identity",
            "library.identity::[int, bool]",
            "library.public::[int]",
        ]:
            with self.subTest(expression=expression), self.assertRaises(PixelError):
                self.compile(
                    {
                        "main.pxl": 'import "library.pxl" as library\neffect fn main() { let value = '
                        + expression
                        + " }",
                        "library.pxl": "fn hidden() {}\nexport fn public() {}\nexport fn identity[T](value: T) -> T = value",
                    }
                )

    def test_named_value_and_callback_navigation(self):
        code = "fn double(n: int) -> int = n * 2\neffect fn main() { let operation = double; print(operation(21)) }"
        index = self.index({"main.pxl": code})
        self.assertEqual(
            len(index.rename("main.pxl", code.index("double"), "twice", analyze=self.index)), 2
        )
        self.assertEqual(
            len(index.rename("main.pxl", code.index("operation"), "callback", analyze=self.index)), 2
        )


if __name__ == "__main__":
    unittest.main()
