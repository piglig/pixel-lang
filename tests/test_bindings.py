import unittest

from pixellang.model import PixelError
from pixellang.compiler_client import CompilerClient
from pixellang.vm import VM


class BindingTests(unittest.TestCase):
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

    def test_var_survives_pixel_image_and_text_recovery(self):
        code = "effect fn main() { var total: int = 0; for value in [1, 2, 3] { total += value }; print(total) }"
        files = {"main.pxl": code}
        self.assertEqual(VM(self.compile(files)).run(), [6])
        document = self.request('pixels', files=files)['document']
        image = self.request('export-pixel-png', document=document)['image']
        decoded = self.request('compile-png', image=image)['bytecode']
        self.assertEqual(VM(decoded).run(), [6])
        recovered = self.request('recover-png', image=image)
        self.assertRegex(recovered["files"][recovered["entry"]], r"\bvar\s+\w+\s*:\s*int\s*=\s*0")
        self.assertEqual(VM(self.request('compile', files=recovered['files'], entry=recovered['entry'])['bytecode']).run(), [6])

    def test_let_parameter_and_iteration_rebinding_rejected(self):
        cases = [
            "effect fn main() { let n = 1; n = 2 }",
            "effect fn main() { let n = 1; n += 2 }",
            "fn change(n: int) { n = 2 }\neffect fn main() { change(1) }",
            "effect fn main() { for n in [1, 2] { n = 3 } }",
            'effect fn main() { match parseInt("x") { .Ok(value) => {}, .Err(error) => { error = error } } }',
        ]
        for source in cases:
            with (
                self.subTest(source=source),
                self.assertRaisesRegex(PixelError, "immutable"),
            ):
                self.compile({"main.pxl": source})

    def test_var_allows_local_collection_and_record_updates(self):
        code = """record Box { value: int }
effect fn main() {
 var values: [int] = [1]
 values.append(2)
 values[0] = 3
 var totals: map[string, int] = map[string, int]{"x": 1}
 totals["x"] = 4
 var box: Box = Box{value: 1}
 box.value = 5
 print(values)
 print(totals["x"])
 print(box.value)
}"""
        self.assertEqual(
            VM(self.compile({"main.pxl": code})).run(), [[3, 2], 4, 5]
        )

    def test_shadowing_uses_nearest_binding_mutability(self):
        good = (
            "effect fn main() { let n = 1; if true { var n = 2; n += 1; print(n) }; print(n) }"
        )
        self.assertEqual(
            VM(self.compile({"main.pxl": good})).run(), [3, 1]
        )
        bad = "effect fn main() { var n = 1; if true { let n = 2; n += 1 } }"
        with self.assertRaisesRegex(PixelError, "immutable"):
            self.compile({"main.pxl": bad})


if __name__ == "__main__":
    unittest.main()
