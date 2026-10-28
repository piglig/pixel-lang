import copy
import math
import unittest

from pixellang.codec import encode_token
from pixellang.debugvalues import inspect_values
from pixellang.model import PixelError
from pixellang.compiler_client import CompilerClient
from pixellang.temporal import Timeline
from pixellang.vm import VM


class Float64Tests(unittest.TestCase):
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

    def run_code(self, body):
        return VM(
            self.compile({"main.pxl": "effect fn main() { " + body + " }"})
        ).run()

    def test_arithmetic_conversions_and_arrays(self):
        self.assertEqual(
            self.run_code("""
var a: [float64] = [1.5, 2e1, 1_000.5]
a[0] *= 2.0
print(a)
print(float64(3) / 2.0)
print(int(-2.75))
print(-5.5 % 2.0)
print(5.5 % -2.0)
print(-5 / 2)
print(1.0 < 1.5)
print(-0.0 == 0.0)
match parseFloat64("-1.25e2") { .Ok(value) => { print(value) }, .Err(error) => { print(error) } }
"""),
            [[3.0, 20.0, 1000.5], 1.5, -2, -1.5, 1.5, -2, True, True, -125.0],
        )

    def test_explicit_precision_boundaries(self):
        self.assertEqual(
            self.run_code("""
print(int(float64(9007199254740993)))
print(int(-9223372036854775808.0))
print(5e-324)
print(1e-999)

"""),
            [9007199254740992, -(2**63), 5e-324, 0.0],
        )

        with self.assertRaises(PixelError):
            self.run_code("print(int(9223372036854775808.0))")

    def test_negative_zero_and_finite_extremes_survive_pixels_and_pool(self):
        code = "effect fn main() { print(0.0); print(-0.0); print(1.7976931348623157e308); print(5e-324) }"
        files = {"main.pxl": code}
        compiled = self.compile(files)
        image = self.request('export-png', files=files)['image']
        recovered = self.request('recover-png', image=image)
        for bytecode in [
            compiled,
            self.request('compile-png', image=image)['bytecode'],
            self.request('compile', **recovered)['bytecode'],
        ]:
            values = VM(bytecode).run()
            self.assertEqual(
                [v.hex() for v in values],
                [
                    (0.0).hex(),
                    (-0.0).hex(),
                    float.fromhex("0x1.fffffffffffffp+1023").hex(),
                    (5e-324).hex(),
                ],
            )

    def test_json_target_types_and_paths(self):
        source = """record Sample { mean: float64 }
effect fn RunTest()->Result[int,Error] {
 let decoded = jsonDecode::[Sample]("{\\"mean\\":1.25}")
 let sample = (decoded)?
 print(sample.mean)
 let parsed = (jsonParse("2.5e1"))?
 print((jsonAsFloat64(parsed))?)
 print(jsonStringify(jsonFrom([-0.0, 1.25])))
 match jsonDecode::[[float64]]("[1e999]") { .Ok(value) => { print(value) }, .Err(error) => { print(error.path); print(error.message) } }

 return .Ok(0)
}
effect fn main(){match RunTest(){.Ok(done)=>{},.Err(error)=>{print(error)}}}
"""
        result = VM(self.compile({"main.pxl": source})).run()
        self.assertEqual(result[:3], [1.25, 25.0, "[-0.0,1.25]"])
        self.assertIn("$[0]", result[3])
        self.assertIn("float64", result[4])

    def test_rejected_literals_mixed_types_and_conversions(self):
        for expr in [
            "1e",
            "1e+",
            "1.2_",
            "1e999",
            "1.0 + 1",
            "1 == 1.0",
            "float64(true)",
            'int("2")',
        ]:
            with self.subTest(expr=expr), self.assertRaises(PixelError):
                self.run_code("print(" + expr + ")")
        self.assertEqual(
            self.run_code("""
match parseFloat64("NaN") { .Ok(value) => { print(value) }, .Err(error) => { print("invalid") } }
match parseFloat64("1e999") { .Ok(value) => { print(value) }, .Err(error) => { print("range") } }
"""),
            ["invalid", "range"],
        )
        with self.assertRaises(PixelError):
            self.run_code("print(1.0 / -0.0)")
        with self.assertRaisesRegex(PixelError, "finite range"):
            self.run_code(
                'print(1e308 * 2.0)'
            )

    def test_untrusted_nonfinite_constants_and_pixels_rejected(self):
        compiled = self.compile({"main.pxl": "effect fn main() { print(1.0) }"})
        for value in [math.inf, -math.inf, math.nan]:
            with self.subTest(value=value):
                bytecode = copy.deepcopy(compiled)
                next(item for item in bytecode["constants"] if item["type"] == "float64")["value"] = value
                with self.assertRaises(PixelError):
                    VM(bytecode)
                with self.assertRaises(PixelError):
                    encode_token("float64", value)

    def test_debug_values_and_source_free_replay(self):
        compiled = self.compiler.debug_project(
            {
                "main.pxl": "effect fn main() { var values: [float64] = [-0.0, 1.25]; values[1] += 0.5; print(values) }"
            }
        )
        timeline = Timeline(compiled.source, compiled=compiled)
        snapshots = [timeline.vm.state()]
        saw_float = False
        while not timeline.vm.halted:
            timeline.advance()
            snapshots.append(timeline.vm.state())
            variables = inspect_values(
                timeline, {"z": timeline.cursor, "path": ["stack"]}
            )["variables"]
            saw_float |= any(v["type"] == "float64" for v in variables)
        self.assertTrue(saw_float)
        restored = Timeline.restore(timeline.document())
        for z in reversed(range(len(snapshots))):
            self.assertEqual(restored.seek(z), snapshots[z])
        output = restored.seek(len(snapshots) - 1)["output"][0]
        self.assertEqual(output[0].hex(), (-0.0).hex())
        self.assertEqual(output[1], 1.75)


if __name__ == "__main__":
    unittest.main()
