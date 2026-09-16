import csv
import io
import json
import unittest

from pixellang.compiler import compile_source
from pixellang.picture import decode_picture, encode_picture
from pixellang.project import compile_project, project_text
from pixellang.vm import VM


class StandardLibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.csv_source, cls.csv_program = compile_project(
            {
                "main.pxl": 'import "std/csv.pxl" as csv\nfn main() {\ntry {\n    let rows = csv.Parse(input())\n    print(rows)\n    print(csv.Parse(csv.Encode(rows)))\n} catch error { print(error.message) }\n}'
            }
        )

    def test_csv_valid_fields_match_independent_parser(self):
        for text in [
            "",
            "\n",
            ",",
            "a,b,\n",
            '"a,b","say ""hi"""\r\n',
            '"two\nlines",猫🌍\r\n-3,\r\n',
            "a\rb\rc",
            '"", "not quoted"',
        ]:
            with self.subTest(text=text):
                if text == '"", "not quoted"':
                    continue  # Deliberately invalid in our strict quote grammar; tested below.
                expected = list(csv.reader(io.StringIO(text, newline="")))
                # A blank record has one empty field in the documented PixelLang dialect.
                expected = [row or [""] for row in expected]
                actual = VM(self.csv_program.bytecode, input_text=text).run()
                self.assertEqual(actual, [expected, expected])

    def test_csv_malformed_data_is_recoverable(self):
        for text in ['"unterminated', 'a"b', '"a"b', '"", "not quoted"']:
            with self.subTest(text=text):
                actual = VM(self.csv_program.bytecode, input_text=text).run()
                self.assertEqual(len(actual), 1)
                self.assertIsInstance(actual[0], str)
                self.assertTrue(actual[0].startswith("CSV "))

    def test_stdlib_is_bundled_as_pixels_and_recovered(self):
        restored = decode_picture(encode_picture(self.csv_source))
        sources = project_text(restored)
        self.assertIn("std/csv.pxl", sources["files"])
        self.assertIn("fn Parse", sources["files"]["std/csv.pxl"])
        output = VM(compile_source(restored).bytecode, input_text='a,"b,c"').run()
        self.assertEqual(output, [[["a", "b,c"]], [["a", "b,c"]]])
        rebuilt = compile_project(sources["files"], sources["entry"])[1]
        self.assertEqual(VM(rebuilt.bytecode, input_text='a,"b,c"').run(), output)

    def test_sort_stats_negative_duplicate_and_empty(self):
        _, compiled = compile_project(
            {
                "main.pxl": 'import "std/sort.pxl" as sort\nimport "std/stats.pxl" as stats\nfn main() {\nlet values = [3, -8, 3, 0, -1]\nprint(sort.Ints(values))\nprint(values)\nprint(stats.Summary(values))\nprint(sort.Ints([]: [int]))\ntry { print(stats.Summary([]: [int])) } catch err { print(err.kind) }\n}'
            }
        )
        self.assertEqual(
            VM(compiled.bytecode).run(),
            [
                [-8, -1, 0, 3, 3],
                [3, -8, 3, 0, -1],
                {"count": 5, "total": -3, "min": -8, "max": 3, "mean": 0},
                [],
                "user",
            ],
        )

    def test_merge_sort_larger_input(self):
        values = [(n * 73) % 211 - 100 for n in range(300)]
        _, compiled = compile_project(
            {
                "main.pxl": 'import "std/sort.pxl" as sort\nfn main() { print(sort.Ints('
                + json.dumps(values)
                + ")) }"
            }
        )
        self.assertEqual(
            VM(compiled.bytecode, max_steps=200_000).run(), [sorted(values)]
        )
