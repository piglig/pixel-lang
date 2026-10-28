"""Human authoring, real data programs and standalone executable image contracts."""

import copy
import json
import io
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from pixellang.codec import load
from pixellang.compiler_client import CompilerClient, CompiledArtifact
from pixellang.model import PixelError
from pixellang.vm import VM

ROOT = Path(__file__).resolve().parents[1]


class NativeCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def request(self, operation, **args):
        return self.compiler.request(operation, **args)['result']

    def project(self, files, entry='main.pxl'):
        compiled = self.compiler.debug_project(files, entry)
        projection = self.request("debug-pixels", files=files, entry=entry)["document"]
        return projection, compiled

    def run_source(self, text, input_text=''):
        return VM(self.compiler.debug_project({'main.pxl': text}).bytecode, input_text=input_text).run()

    def compile_path(self, path):
        if path.suffix == '.pxl':
            files, entry = self.compiler.read_project(path)
            return self.project(files, entry)[1]
        return CompiledArtifact(self.request('compile-png', image=path.read_bytes())['bytecode'])

    def encode(self, doc, scale=8):
        return self.request('export-pixel-png', document=doc, scale=scale)['image']

    def compile_pixels(self, doc):
        return CompiledArtifact(self.request('compile-pixels', document=doc)['bytecode'])

    def recover(self, doc):
        return self.request('recover-pixels', document=doc)

class HumanSourceTests(NativeCase):
    def test_runtime_allocation_and_output_limits(self):
        for program, data, message in (
            (
                'effect fn main() {\nprint(join(([input(), input()]: [string]), ""))\n}',
                "x" * 500001,
                "String size",
            ),
            (
                'effect fn main() {\nprint(replace(input(), "x", "xxx"))\n}',
                "x" * 400000,
                "String size",
            ),
            ("effect fn main() {\nprint(input())\nprint(input())\n}", "x" * 600000, "Output"),
        ):
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(PixelError, message),
            ):
                self.run_source(program, data)

    def test_unit_array_elements_rejected(self):
        with self.assertRaises(PixelError):
            self.run_source("fn nothing() { return }\neffect fn main() {\nlet values = [nothing()]\n}")

    def test_uniform_entry_and_inference(self):
        self.assertEqual(
            self.run_source("effect fn main() {\nvar answer: int = 40\nanswer += 2\nprint(answer)\n}"),
            [42],
        )

    def test_unicode_string_escape(self):
        self.assertEqual(
            self.run_source(
                'effect fn main() {\nlet text = "Hello, 世界\\n\\"pixel\\""\nprint(text)\nprint(len("世界"))\n}'
            ),
            ['Hello, 世界\n"pixel"', 2],
        )

    def test_unicode_identifiers(self):
        self.assertEqual(self.run_source("effect fn main() {\nlet 结果 = 21\nprint(结果 * 2)\n}"), [42])

    def test_comments_and_multiline_arrays(self):
        self.assertEqual(
            self.run_source(
                "effect fn main() {\nlet values = ([\n1, // one\n2,\n]: [int])\nprint(values)\n}"
            ),
            [[1, 2]],
        )

    def test_recursion(self):
        self.assertEqual(
            self.run_source(
                "fn factorial(n: int) -> int {\nif n <= 1 { return 1 } else { return n * factorial(n - 1) }\n}\neffect fn main() {\nprint(factorial(10))\n}"
            ),
            [3628800],
        )

    def test_else_if(self):
        self.assertEqual(
            self.run_source(
                "effect fn main() {\nif false { print(1) } else if true { print(2) } else { print(3) }\n}"
            ),
            [2],
        )

    def test_integer_extremes(self):
        self.assertEqual(
            self.run_source(
                "effect fn main() {\nprint(9_223_372_036_854_775_807)\nprint(-9223372036854775808)\n}"
            ),
            [2**63 - 1, -(2**63)],
        )

    def test_string_order_and_logic(self):
        self.assertEqual(
            self.run_source('effect fn main() {\nprint("a" < "b" && !("x" == "y"))\n}'), [True]
        )

    def test_integer_division_uses_no_float(self):
        self.assertEqual(
            self.run_source("effect fn main() {\nprint(9223372036854775807 / 3)\n}"), [(2**63 - 1) // 3]
        )

    def test_void_functions_and_expression_discard(self):
        self.assertEqual(
            self.run_source('effect fn greet() { print("HELLO"); return }\neffect fn main() {\ngreet()\n}'),
            ["HELLO"],
        )

    def test_arrays_update_through_explicit_inout(self):
        self.assertEqual(
            self.run_source(
                "fn update(values: inout [int]) { values[0] = 42 }\neffect fn main() {\nvar values: [int] = [1]\nupdate(&values)\nprint(values)\n}"
            ),
            [[42]],
        )

    def test_nested_arrays(self):
        self.assertEqual(
            VM(self.compile_path(ROOT / "examples/matrix/main.pxl").bytecode).run(),
            [[[19, 22], [43, 50]]],
        )

    def test_empty_array_in_return_and_argument(self):
        self.assertEqual(
            self.run_source(
                "fn make() -> [int] { return ([]: [int]) }\nfn count(values: [int]) -> int { return len(values) }\neffect fn main() {\nprint(count(make()))\nprint(count(([]: [int])))\n}"
            ),
            [0, 0],
        )

    def test_value_copies_and_print_snapshots(self):
        self.assertEqual(
            self.run_source(
                "effect fn main() {\nlet a = [1]\nvar b = a\nprint(a)\nb[0] = 2\nprint(a)\nprint(b)\n}"
            ),
            [[1], [1], [2]],
        )

    def test_builtins(self):
        self.assertEqual(
            self.run_source(
                'effect fn RunTest()->Result[int,Error] {\nvar a = split("a,b,c", ",")\nprint(join(slice(a, 1, 3), "-"))\nprint(match a.pop() { Option.Some(value) => value, Option.None => "missing" })\na.append("d"); print(a)\nprint((parseInt("-42"))?)\nprint(slice("世界你好", 1, 3))\nprint(replace(trim(" A-B "), "-", "+"))\n\n return .Ok(0)\n}\neffect fn main(){match RunTest(){.Ok(done)=>{},.Err(error)=>{print(error.code)}}}\n'
            ),
            ["b-c", "c", ["a", "b", "d"], -42, "界你", "A+B"],
        )

    def test_input_is_runtime_not_source(self):
        _, c = self.project(
            {"main.pxl": 'effect fn main() {\nprint("Data: " + input())\n}'}
        )
        self.assertEqual(VM(c.bytecode, input_text="one").run(), ["Data: one"])
        self.assertEqual(VM(c.bytecode, input_text="two").run(), ["Data: two"])

    def test_readable_static_diagnostic(self):
        with self.assertRaises(PixelError) as cm:
            self.run_source("effect fn main() {\nvar count: int = true\n}")
        error = cm.exception.to_dict()
        self.assertEqual(error["phase"], "check")
        self.assertEqual(error["span"]["kind"], "text")
        self.assertEqual(error["span"]["start"]["line"], 2)

    def test_runtime_text_provenance(self):
        with self.assertRaises(PixelError) as cm:
            self.run_source("effect fn main() {\nlet values = ([1]: [int])\nprint(values[9])\n}")
        self.assertEqual(cm.exception.to_dict()["span"]["text"]["source"], "main.pxl")
        self.assertIn("main.pxl:3:", str(cm.exception))

    def test_language_syntax_errors(self):
        for text in (
            "var x: int =",
            'print("unterminated)',
            "/* unterminated",
            "effect fn main() {",
            "let x = 1\n@",
            "let x = 1__2",
            'import "../escape.pxl"',
        ):
            with self.subTest(text=text), self.assertRaises(PixelError):
                self.run_source(text)

    def test_type_errors(self):
        for text in (
            "effect fn main() {\nlet x = []\n}",
            "effect fn main() {\nlet x = ([true]: [int])\n}",
            'effect fn main() {\nprint(1 + "2")\n}',
            "effect fn main() {\nvar x = [1]\nx[true] = 1\n}",
            'effect fn main() {\nlet x = "abc"\nx[0] = "d"\n}',
            "effect fn main() {\nprint(len(1))\n}",
            "effect fn main() {\nvar x: [int] = []; x.append(true)\n}",
            "effect fn main() {\nprint(9223372036854775808)\n}",
        ):
            with self.subTest(text=text), self.assertRaises(PixelError):
                self.run_source(text)

    def test_runtime_errors(self):
        for text in (
            "effect fn main() {\nprint(1 / 0)\n}",
            'effect fn main() {\nprint(slice("x",0,2))\n}',
            "effect fn main() {\nassert(false)\n}",
            "effect fn main() {\nprint(9223372036854775807 + 1)\n}",
            "effect fn main() {\nprint(([1]: [int])[-1])\n}",
        ):
            with self.subTest(text=text), self.assertRaises(PixelError):
                self.run_source(text)

    def test_recoverable_absence_and_parse_failure_are_values(self):
        self.assertEqual(self.run_source('''effect fn main() {
 var empty: [int] = []
 print(match empty.pop() { Option.None => "empty", Option.Some(value) => "unexpected" })
 print(match parseInt("12x") { .Err(error) => "invalid", .Ok(value) => "unexpected" })
}'''), ["empty", "invalid"])

    def test_nested_import_is_public_diagnostic(self):
        with self.assertRaisesRegex(PixelError, "module-level"):
            self.run_source('effect fn main() { import "a.pxl" }')

    def test_module_visibility_and_missing_file(self):
        for sources in (
            {"main.pxl": 'import "missing.pxl"'},
            {
                "main.pxl": 'import "lib.pxl"\neffect fn main() {\nprint(lib.hidden())\n}',
                "lib.pxl": "fn hidden() -> int { return 1 }",
            },
        ):
            with self.assertRaises(PixelError):
                self.project(sources)

    def test_relative_modules_and_aliases(self):
        files = {
            "main.pxl": 'import "lib/math.pxl" as arithmetic\neffect fn main() {\nprint(arithmetic.Double(21))\n}',
            "lib/math.pxl": 'import "more.pxl"\nexport fn Double(x: int) -> int { return more.Multiply(x, 2) }',
            "lib/more.pxl": "export fn Multiply(a: int, b: int) -> int { return a * b }",
        }
        doc, c = self.project(files)
        self.assertEqual(VM(c.bytecode).run(), [42])
        recovered = self.recover(doc)
        self.assertEqual(
            VM(self.project(recovered["files"], recovered["entry"])[1].bytecode).run(), [42]
        )


class StandaloneImageTests(NativeCase):
    def test_real_project_roundtrip_without_metadata_or_files(self):
        files, entry = self.compiler.read_project(ROOT / "examples/data-report/main.pxl")
        doc, c = self.project(files, entry)
        expected = VM(c.bytecode, input_text="9,2,5").run()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.png"
            path.write_bytes(self.encode(doc))
            with Image.open(path) as original:
                self.assertEqual(original.info, {})
                stripped = Image.frombytes("RGBA", original.size, original.tobytes())
                stripped.save(path)
            self.assertEqual(list(Path(tmp).iterdir()), [path])
            self.assertEqual(
                VM(self.compile_path(path).bytecode, input_text="9,2,5").run(), expected
            )
            recovered = self.request('recover-png', image=path.read_bytes())
            self.assertEqual(len(recovered["files"]), len(files))
            self.assertEqual(
                VM(
                    self.project(recovered["files"], recovered["entry"])[1].bytecode,
                    input_text="9,2,5",
                ).run(),
                expected,
            )

    def test_image_is_deterministic(self):
        doc, _ = self.project({"main.pxl": 'effect fn main() {\nprint("deterministic")\n}'})
        a, b = self.encode(doc), self.encode(doc)
        self.assertEqual(a, b)

    def test_scale_does_not_change_meaning(self):
        doc, _ = self.project(
            {"main.pxl": "effect fn main() {\nprint(([8,3,5]: [int]))\n}"}
        )
        for scale in (4, 8, 16, 24):
            self.assertEqual(
                VM(
                    self.request('compile-png', image=self.encode(doc, scale))['bytecode']
                ).run(),
                [[8, 3, 5]],
            )

    def test_corrupt_header_rejected(self):
        doc, _ = self.project({"main.pxl": "effect fn main() {\nprint(42)\n}"})
        image = Image.open(io.BytesIO(self.encode(doc))).copy()
        r, g, b, a = image.getpixel((8, 0))
        image.putpixel((8, 0), (r ^ 1, g, b, a))
        with self.assertRaises(PixelError):
            buffer = io.BytesIO(); image.save(buffer, format='PNG')
            self.request('compile-png', image=buffer.getvalue())

    def test_nonuniform_tile_rejected(self):
        doc, _ = self.project({"main.pxl": "effect fn main() {\nprint(42)\n}"})
        image = Image.open(io.BytesIO(self.encode(doc))).copy()
        # The first module starts below the physical header. Find a bright semantic tile.
        found = None
        for y in range(20, image.height):
            for x in range(image.width):
                if image.getpixel((x, y))[:3] != (16, 20, 24):
                    found = (x, y)
                    break
            if found:
                break
        color = image.getpixel(found)
        image.putpixel(found, (color[0], color[1] ^ 1, color[2], 255))
        with self.assertRaisesRegex(PixelError, "Nonuniform"):
            buffer = io.BytesIO(); image.save(buffer, format='PNG')
            self.request('compile-png', image=buffer.getvalue())

    def test_normalized_bundle_preserves_dependencies(self):
        files, entry = self.compiler.read_project(ROOT / "examples/data-report/main.pxl")
        doc, c = self.project(files, entry)
        normalized = self.request('normalize-pixels', document=doc)['document']
        self.assertEqual(
            VM(self.compile_pixels(normalized).bytecode).run(), VM(c.bytecode).run()
        )
        self.assertEqual(self.request('normalize-pixels', document=normalized)['document'], normalized)

    def test_direct_grid_can_be_an_executable_image(self):
        doc = load(ROOT / "examples/addition.pixel")
        self.assertEqual(
            VM(self.request('compile-png', image=self.encode(doc))['bytecode']).run(), [30]
        )

    def test_recovered_names_follow_optional_metadata_not_original_text(self):
        original = "// My original comment\neffect fn main() {\nvar answer: int = 42\nprint(answer)\n}"
        doc, _ = self.project({"main.pxl": original})
        for preserve_names in (True, False):
            with self.subTest(preserve_names=preserve_names):
                source = copy.deepcopy(doc)
                if not preserve_names:
                    source['metadata'] = {}
                    for module in source.get('bundle', {}).get('modules', {}).values():
                        module['metadata'] = {}
                recovered = self.request('recover-png', image=self.encode(source))
                text = ''.join(recovered['files'].values())
                self.assertNotIn('My original comment', text)
                if preserve_names:
                    self.assertIn('answer', text)
                else:
                    self.assertNotIn('answer', text)
                self.assertEqual(VM(self.project(recovered['files'], recovered['entry'])[1].bytecode).run(), [42])




class RealAlgorithmTests(NativeCase):
    def test_sort_against_reference(self):
        files, entry = self.compiler.read_project(ROOT / "examples/data-report/main.pxl")
        _, compiled = self.project(files, entry)
        rng = random.Random(2026)
        for count in (1, 3, 11, 30):
            values = [rng.randrange(-1000, 1000) for _ in range(count)]
            result = VM(
                compiled.bytecode,
                max_steps=1000000,
                input_text=",".join(map(str, values)),
            ).run()
            self.assertEqual(result[2], sorted(values))
            self.assertEqual(result[3], f"samples={count}")

    def test_negative_mean(self):
        compiled = self.compile_path(ROOT / "examples/data-report/main.pxl")
        output = VM(compiled.bytecode, input_text="-1, 0, 0").run()
        self.assertIn("mean=-0.33", output[1])

    def test_word_frequencies(self):
        compiled = self.compile_path(ROOT / "examples/word-count/main.pxl")
        self.assertEqual(
            VM(compiled.bytecode, input_text="red blue red green blue red").run(),
            ["red: 3", "blue: 2", "green: 1"],
        )

    def test_cli_pack_run_unpack(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "program.png"
            out = Path(tmp) / "recovered"

            def cli(*args):
                return subprocess.run(
                    [sys.executable, "-m", "pixellang", *map(str, args)],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            packed = cli("pack", "examples/data-report/main.pxl", "-o", image)
            self.assertEqual(packed.returncode, 0, packed.stderr)
            result = cli(
                "run",
                image,
                "--input",
                "examples/data-report/measurements.csv",
                "--json",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["output"][2], [1, 2, 3, 4, 8])
            restored = cli("unpack", image, "-o", out)
            self.assertEqual(restored.returncode, 0, restored.stderr)
            candidates = [p for p in out.glob('*.pxl') if 'effect fn main()' in p.read_text()]
            self.assertEqual(len(candidates), 1)
            self.assertEqual(cli("run", candidates[0]).returncode, 0)
            self.assertEqual(cli("unpack", image, "-o", out).returncode, 1)

    def test_cli_preserves_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "main.pxl"
            path.write_text('effect fn main() {\nprint("PixelLang HELLO")\n}')
            result = subprocess.run(
                [sys.executable, "-m", "pixellang", "run", str(path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.stdout.strip(), "PixelLang HELLO")


if __name__ == "__main__":
    unittest.main()
