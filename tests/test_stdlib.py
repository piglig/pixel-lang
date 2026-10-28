import csv
import io
import json
import unittest

from pixellang.vm import VM
from pixellang.compiler_client import CompilerClient


class StandardLibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)
        cls.csv_program = cls.compiler.debug_project(
            {
                "main.pxl": 'import "std/csv.pxl" as csv\neffect fn main() {\n match Roundtrip() { .Ok(value) => {}, .Err(error) => print(error.message) }\n}\neffect fn Roundtrip() -> Result[int, Error] {\n let rows = csv.Parse(input())?\n print(rows)\n print(csv.Parse(csv.Encode(rows)?)?)\n return .Ok(0)\n}'
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
        image = self.compiler.request('export-png', **self.csv_program.source_project)['result']['image']
        sources = self.compiler.request('recover-png', image=image)['result']
        self.assertEqual(len(sources['files']), 3)
        for name, text in sources['files'].items():
            info = self.compiler.request('module-info', files={name: text}, entry=name)['result']
            for imported in info['imports']:
                self.assertIn(imported, sources['files'])
        recovered_bytecode = self.compiler.request('compile-png', image=image)['result']['bytecode']
        output = VM(recovered_bytecode, input_text='a,"b,c"').run()
        self.assertEqual(output, [[["a", "b,c"]], [["a", "b,c"]]])
        rebuilt = self.compiler.request('compile', **sources)['result']['bytecode']
        self.assertEqual(VM(rebuilt, input_text='a,"b,c"').run(), output)

    def test_sort_stats_negative_duplicate_and_empty(self):
        compiled = self.compiler.debug_project(
            {
                "main.pxl": 'import "std/collections.pxl" as collections\nimport "std/stats.pxl" as stats\neffect fn main() {\n let values = [3, -8, 3, 0, -1]\n print(collections.Sorted(values))\n print(values)\n match stats.Summary(values) { Option.Some(value) => print(value), Option.None => print("empty") }\n let empty: [int] = []\n print(collections.Sorted(empty))\n match stats.Summary(empty) { Option.Some(value) => print(value), Option.None => print("empty") }\n}'
            }
        )
        self.assertEqual(
            VM(compiled.bytecode).run(),
            [
                [-8, -1, 0, 3, 3],
                [3, -8, 3, 0, -1],
                {"count": 5, "total": -3, "min": -8, "max": 3, "mean": 0},
                [],
                "empty",
            ],
        )

    def test_merge_sort_larger_input(self):
        values = [(n * 73) % 211 - 100 for n in range(300)]
        compiled = self.compiler.debug_project(
            {
                "main.pxl": 'import "std/collections.pxl" as collections\neffect fn main() { print(collections.Sorted('
                + json.dumps(values)
                + ")) }"
            }
        )
        self.assertEqual(
            VM(compiled.bytecode, max_steps=200_000).run(), [sorted(values)]
        )
