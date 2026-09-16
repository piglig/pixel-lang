"""Human authoring, real data programs and standalone executable image contracts."""

import json
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from pixellang.codec import load
from pixellang.compiler import compile_file, compile_source, normalize
from pixellang.model import PixelError
from pixellang.picture import decode_picture, encode_picture, save_picture
from pixellang.project import compile_project, project_text, read_project
from pixellang.vm import VM

ROOT = Path(__file__).resolve().parents[1]


def run(text, input_text=""):
    _, result = compile_project({"main.pxl": text})
    return VM(result.bytecode, input_text=input_text).run()


class HumanSourceTests(unittest.TestCase):
    def test_runtime_allocation_and_output_limits(self):
        for program, data, message in (
            (
                'fn main() {\nprint(join(([input(), input()]: [string]), ""))\n}',
                "x" * 500001,
                "String size",
            ),
            (
                'fn main() {\nprint(replace(input(), "x", "xxx"))\n}',
                "x" * 400000,
                "String size",
            ),
            ("fn main() {\nprint(input())\nprint(input())\n}", "x" * 600000, "Output"),
        ):
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(PixelError, message),
            ):
                run(program, data)

    def test_unit_array_elements_rejected(self):
        with self.assertRaises(PixelError):
            run("fn nothing() { return }\nfn main() {\nlet values = [nothing()]\n}")

    def test_uniform_entry_and_inference(self):
        self.assertEqual(
            run("fn main() {\nvar answer: int = 40\nanswer += 2\nprint(answer)\n}"),
            [42],
        )

    def test_unicode_string_escape(self):
        self.assertEqual(
            run(
                'fn main() {\nlet text = "Hello, 世界\\n\\"pixel\\""\nprint(text)\nprint(len("世界"))\n}'
            ),
            ['Hello, 世界\n"pixel"', 2],
        )

    def test_unicode_identifiers(self):
        self.assertEqual(run("fn main() {\nlet 结果 = 21\nprint(结果 * 2)\n}"), [42])

    def test_comments_and_multiline_arrays(self):
        self.assertEqual(
            run(
                "fn main() {\nlet values = ([\n1, // one\n2,\n]: [int])\nprint(values)\n}"
            ),
            [[1, 2]],
        )

    def test_recursion(self):
        self.assertEqual(
            run(
                "fn factorial(n: int) -> int {\nif n <= 1 { return 1 } else { return n * factorial(n - 1) }\n}\nfn main() {\nprint(factorial(10))\n}"
            ),
            [3628800],
        )

    def test_else_if(self):
        self.assertEqual(
            run(
                "fn main() {\nif false { print(1) } else if true { print(2) } else { print(3) }\n}"
            ),
            [2],
        )

    def test_integer_extremes(self):
        self.assertEqual(
            run(
                "fn main() {\nprint(9_223_372_036_854_775_807)\nprint(-9223372036854775808)\n}"
            ),
            [2**63 - 1, -(2**63)],
        )

    def test_string_order_and_logic(self):
        self.assertEqual(
            run('fn main() {\nprint("a" < "b" && !("x" == "y"))\n}'), [True]
        )

    def test_integer_division_uses_no_float(self):
        self.assertEqual(
            run("fn main() {\nprint(9223372036854775807 / 3)\n}"), [(2**63 - 1) // 3]
        )

    def test_void_functions_and_expression_discard(self):
        self.assertEqual(
            run('fn greet() { print("HELLO"); return }\nfn main() {\ngreet()\n}'),
            ["HELLO"],
        )

    def test_arrays_pass_by_reference(self):
        self.assertEqual(
            run(
                "fn update(values: [int]) { values[0] = 42 }\nfn main() {\nlet values = ([1]: [int])\nupdate(values)\nprint(values)\n}"
            ),
            [[42]],
        )

    def test_nested_arrays(self):
        self.assertEqual(
            VM(compile_file(ROOT / "examples/matrix/main.pxl").bytecode).run(),
            [[[19, 22], [43, 50]]],
        )

    def test_empty_array_in_return_and_argument(self):
        self.assertEqual(
            run(
                "fn make() -> [int] { return ([]: [int]) }\nfn count(values: [int]) -> int { return len(values) }\nfn main() {\nprint(count(make()))\nprint(count(([]: [int])))\n}"
            ),
            [0, 0],
        )

    def test_aliasing_and_print_snapshots(self):
        self.assertEqual(
            run(
                "fn main() {\nlet a = ([1]: [int])\nlet b = a\nprint(a)\nb[0] = 2\nprint(a)\n}"
            ),
            [[1], [2]],
        )

    def test_builtins(self):
        self.assertEqual(
            run(
                'fn main() {\nlet a = split("a,b,c", ",")\nprint(join(slice(a, 1, 3), "-"))\nprint(pop(a))\nprint(append(a, "d"))\nprint(parseInt("-42"))\nprint(slice("世界你好", 1, 3))\nprint(replace(trim(" A-B "), "-", "+"))\n}'
            ),
            ["b-c", "c", ["a", "b", "d"], -42, "界你", "A+B"],
        )

    def test_input_is_runtime_not_source(self):
        _, c = compile_project(
            {"main.pxl": 'fn main() {\nprint("Data: " + input())\n}'}
        )
        self.assertEqual(VM(c.bytecode, input_text="one").run(), ["Data: one"])
        self.assertEqual(VM(c.bytecode, input_text="two").run(), ["Data: two"])

    def test_readable_static_diagnostic(self):
        with self.assertRaises(PixelError) as cm:
            run("fn main() {\nvar count: int = true\n}")
        error = cm.exception.to_dict()
        self.assertEqual(error["phase"], "type")
        self.assertEqual(error["span"]["kind"], "text")
        self.assertEqual(error["span"]["start"]["line"], 2)

    def test_runtime_text_provenance(self):
        with self.assertRaises(PixelError) as cm:
            run("fn main() {\nlet values = ([1]: [int])\nprint(values[9])\n}")
        self.assertEqual(cm.exception.to_dict()["span"]["text"]["source"], "main.pxl")
        self.assertIn("main.pxl:3:", str(cm.exception))

    def test_language_syntax_errors(self):
        for text in (
            "var x: int =",
            'print("unterminated)',
            "/* unterminated",
            "fn main() {",
            "let x = 1\n@",
            "let x = 1__2",
            'import "../escape.pxl"',
        ):
            with self.subTest(text=text), self.assertRaises(PixelError):
                run(text)

    def test_type_errors(self):
        for text in (
            "fn main() {\nlet x = []\n}",
            "fn main() {\nlet x = ([true]: [int])\n}",
            'fn main() {\nprint(1 + "2")\n}',
            "fn main() {\nlet x = ([1]: [int])\nx[true] = 1\n}",
            'fn main() {\nlet x = "abc"\nx[0] = "d"\n}',
            "fn main() {\nprint(len(1))\n}",
            "fn main() {\nprint(append(([]: [int]),true))\n}",
            "fn main() {\nprint(9223372036854775808)\n}",
        ):
            with self.subTest(text=text), self.assertRaises(PixelError):
                run(text)

    def test_runtime_errors(self):
        for text in (
            "fn main() {\nprint(1 / 0)\n}",
            "fn main() {\nprint(pop(([]: [int])))\n}",
            'fn main() {\nprint(parseInt("12x"))\n}',
            'fn main() {\nprint(slice("x",0,2))\n}',
            "fn main() {\nassert(false)\n}",
            "fn main() {\nprint(9223372036854775807 + 1)\n}",
            "fn main() {\nprint(([1]: [int])[-1])\n}",
        ):
            with self.subTest(text=text), self.assertRaises(PixelError):
                run(text)

    def test_nested_import_is_public_diagnostic(self):
        with self.assertRaisesRegex(PixelError, "module-level"):
            run('fn main() { import "a.pxl" }')

    def test_module_visibility_and_missing_file(self):
        for sources in (
            {"main.pxl": 'import "missing.pxl"'},
            {
                "main.pxl": 'import "lib.pxl"\nfn main() {\nprint(lib.hidden())\n}',
                "lib.pxl": "fn hidden() -> int { return 1 }",
            },
        ):
            with self.assertRaises(PixelError):
                compile_project(sources)

    def test_relative_modules_and_aliases(self):
        files = {
            "main.pxl": 'import "lib/math.pxl" as arithmetic\nfn main() {\nprint(arithmetic.Double(21))\n}',
            "lib/math.pxl": 'import "more.pxl"\nexport fn Double(x: int) -> int { return more.Multiply(x, 2) }',
            "lib/more.pxl": "export fn Multiply(a: int, b: int) -> int { return a * b }",
        }
        doc, c = compile_project(files)
        self.assertEqual(VM(c.bytecode).run(), [42])
        recovered = project_text(doc)
        self.assertEqual(
            VM(compile_project(recovered["files"])[1].bytecode).run(), [42]
        )


class StandaloneImageTests(unittest.TestCase):
    def test_real_project_roundtrip_without_metadata_or_files(self):
        files, entry = read_project(ROOT / "examples/data-report/main.pxl")
        doc, c = compile_project(files, entry)
        expected = VM(c.bytecode, input_text="9,2,5").run()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.png"
            save_picture(doc, path)
            with Image.open(path) as original:
                self.assertEqual(original.info, {})
                stripped = Image.frombytes("RGBA", original.size, original.tobytes())
                stripped.save(path)
            self.assertEqual(list(Path(tmp).iterdir()), [path])
            self.assertEqual(
                VM(compile_file(path).bytecode, input_text="9,2,5").run(), expected
            )
            recovered = project_text(load(path))
            self.assertEqual(set(recovered["files"]), set(files))
            self.assertEqual(
                VM(
                    compile_project(recovered["files"], recovered["entry"])[1].bytecode,
                    input_text="9,2,5",
                ).run(),
                expected,
            )

    def test_image_is_deterministic(self):
        doc, _ = compile_project({"main.pxl": 'fn main() {\nprint("deterministic")\n}'})
        a, b = encode_picture(doc), encode_picture(doc)
        self.assertEqual(a.size, b.size)
        self.assertEqual(a.tobytes(), b.tobytes())

    def test_scale_does_not_change_meaning(self):
        doc, _ = compile_project(
            {"main.pxl": "fn main() {\nprint(([8,3,5]: [int]))\n}"}
        )
        for scale in (4, 8, 16, 24):
            self.assertEqual(
                VM(
                    compile_source(decode_picture(encode_picture(doc, scale))).bytecode
                ).run(),
                [[8, 3, 5]],
            )

    def test_corrupt_header_rejected(self):
        doc, _ = compile_project({"main.pxl": "fn main() {\nprint(42)\n}"})
        image = encode_picture(doc)
        r, g, b, a = image.getpixel((8, 0))
        image.putpixel((8, 0), (r ^ 1, g, b, a))
        with self.assertRaises(PixelError):
            decode_picture(image)

    def test_nonuniform_tile_rejected(self):
        doc, _ = compile_project({"main.pxl": "fn main() {\nprint(42)\n}"})
        image = encode_picture(doc)
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
            decode_picture(image)

    def test_normalized_bundle_preserves_dependencies(self):
        files, entry = read_project(ROOT / "examples/data-report/main.pxl")
        doc, c = compile_project(files, entry)
        normalized = normalize(doc)
        self.assertEqual(
            VM(compile_source(normalized).bytecode).run(), VM(c.bytecode).run()
        )
        self.assertEqual(normalize(normalized), normalized)

    def test_direct_grid_can_be_an_executable_image(self):
        doc = load(ROOT / "examples/addition.pixel")
        self.assertEqual(
            VM(compile_source(decode_picture(encode_picture(doc))).bytecode).run(), [30]
        )

    def test_recovered_names_and_code_are_not_original_text(self):
        original = "fn main() {\nvar answer: int = 42\nprint(answer)\n}"
        doc, _ = compile_project({"main.pxl": original})
        recovered = project_text(decode_picture(encode_picture(doc)))["files"][
            "main.pxl"
        ]
        self.assertIn("answer", recovered)
        self.assertNotIn("My original comment", recovered)


class RealAlgorithmTests(unittest.TestCase):
    def test_sort_against_reference(self):
        files, entry = read_project(ROOT / "examples/data-report/main.pxl")
        _, compiled = compile_project(files, entry)
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
        compiled = compile_file(ROOT / "examples/data-report/main.pxl")
        output = VM(compiled.bytecode, input_text="-1, 0, 0").run()
        self.assertIn("mean=-0.33", output[1])

    def test_word_frequencies(self):
        compiled = compile_file(ROOT / "examples/word-count/main.pxl")
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
            self.assertEqual(cli("run", out / "main.pxl").returncode, 0)
            self.assertEqual(cli("unpack", image, "-o", out).returncode, 1)

    def test_cli_preserves_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "main.pxl"
            path.write_text('fn main() {\nprint("PixelLang HELLO")\n}')
            result = subprocess.run(
                [sys.executable, "-m", "pixellang", "run", str(path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.stdout.strip(), "PixelLang HELLO")


if __name__ == "__main__":
    unittest.main()
