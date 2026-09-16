import copy
import math
import unittest

from pixellang.codec import encode_token
from pixellang.compiler import compile_source
from pixellang.debugvalues import inspect_values
from pixellang.model import PixelError
from pixellang.picture import decode_picture, encode_picture
from pixellang.project import compile_project, project_text
from pixellang.temporal import Timeline
from pixellang.vm import VM


class Float64Tests(unittest.TestCase):
    def run_code(self, body):
        return VM(
            compile_project({"main.pxl": "fn main() { " + body + " }"})[1].bytecode
        ).run()

    def test_arithmetic_conversions_and_arrays(self):
        self.assertEqual(
            self.run_code("""
let a: [float64] = [1.5, 2e1, 1_000.5]
a[0] *= 2.0
print(a)
print(float64(3) / 2.0)
print(int(-2.75))
print(-5.5 % 2.0)
print(5.5 % -2.0)
print(-5 / 2)
print(1.0 < 1.5)
print(-0.0 == 0.0)
print(parseFloat64("-1.25e2"))
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
try { print(int(9223372036854775808.0)) } catch e { print("range") }
"""),
            [9007199254740992, -(2**63), 5e-324, 0.0, "range"],
        )

    def test_negative_zero_and_finite_extremes_survive_pixels_and_pool(self):
        code = "fn main() { print(0.0); print(-0.0); print(1.7976931348623157e308); print(5e-324) }"
        source, compiled = compile_project({"main.pxl": code})
        image = decode_picture(encode_picture(source))
        recovered = project_text(image)
        for bytecode in [
            compiled.bytecode,
            compile_source(image).bytecode,
            compile_project(recovered["files"])[1].bytecode,
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
fn main() {
 let sample = jsonDecode[Sample]("{\\"mean\\":1.25}")
 print(sample.mean)
 print(jsonAsFloat64(jsonParse("2.5e1")))
 print(jsonStringify(jsonFrom([-0.0, 1.25])))
 try { print(jsonDecode[[float64]]("[1e999]")) } catch e { print(e.message) }
}"""
        result = VM(compile_project({"main.pxl": source})[1].bytecode).run()
        self.assertEqual(result[:3], [1.25, 25.0, "[-0.0,1.25]"])
        self.assertIn("$[0]", result[3])
        self.assertIn("float64", result[3])

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
try { print(1.0 / -0.0) } catch e { print("zero") }
try { print(parseFloat64("NaN")) } catch e { print("invalid") }
try { print(parseFloat64("1e999")) } catch e { print("range") }
"""),
            ["zero", "invalid", "range"],
        )
        with self.assertRaisesRegex(PixelError, "finite range"):
            self.run_code(
                'try { print(1e308 * 2.0) } catch e { print("must not catch") }'
            )

    def test_untrusted_nonfinite_constants_and_pixels_rejected(self):
        _, compiled = compile_project({"main.pxl": "fn main() { print(1.0) }"})
        for value in [math.inf, -math.inf, math.nan]:
            with self.subTest(value=value):
                bytecode = copy.deepcopy(compiled.bytecode)
                bytecode["constants"][0]["value"] = value
                with self.assertRaises(PixelError):
                    VM(bytecode)
                with self.assertRaises(PixelError):
                    encode_token("float64", value)

    def test_debug_values_and_source_free_replay(self):
        source, _ = compile_project(
            {
                "main.pxl": "fn main() { let values = [-0.0, 1.25]; values[1] += 0.5; print(values) }"
            }
        )
        timeline = Timeline(source)
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
