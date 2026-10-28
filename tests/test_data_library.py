import unittest

from pixellang.compiler_client import CompilerClient
from pixellang.model import PixelError
from pixellang.vm import VM


class DataLibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def request(self, operation, **args):
        response = self.compiler.request(operation, **args)
        self.assertEqual(response['status'], 'ok', response)
        return response['result']

    def test_optional_lookup_map_assignment_and_histogram(self):
        files = (
            {
                "main.pxl": '''import "std/collections.pxl" as collections
record Item { value: int }
effect fn main() {
 let source = map[string, Item]{"first": Item{value: 1}}
 var copied = source
 copied["first"].value = 7
 copied["second"] = Item{value: 9}
 print(source["first"].value)
 print(source.has("second"))
 print(match source.get("absent") { Option.Some(item) => item.value, Option.None => -1 })
 print(match source.get("first") { Option.Some(item) => item.value, Option.None => -1 })
 print(copied["first"].value); print(copied["second"].value)
 var counts: map[string, int] = map[string, int]{}
 collections.Increment(&counts, "b"); collections.Increment(&counts, "a"); collections.Increment(&counts, "b")
 for key, value in counts { print(key); print(value) }
}'''
            }
        )
        expected = [1, False, -1, 1, 7, 9, "b", 2, "a", 1]
        compiled = self.request("compile", files=files)["bytecode"]
        self.assertEqual(VM(compiled).run(), expected)
        image = self.request('export-png', files=files)['image']
        self.assertEqual(VM(self.request('compile-png', image=image)['bytecode']).run(), expected)

    def test_map_copy_does_not_grant_write_permissions(self):
        for update in ['copied["first"].value = 7', 'copied["second"] = Item{value: 9}']:
            code = 'import "std/collections.pxl" as collections\nrecord Item { value: int }\neffect fn main() { let source = map[string, Item]{"first": Item{value: 1}}; let copied = source; ' + update + ' }'
            with self.subTest(update=update), self.assertRaisesRegex(PixelError, 'immutable|readonly'):
                self.request('compile', files={'main.pxl': code})

    def test_physical_lines_preserve_blanks_and_unicode(self):
        compiled = self.request("compile", files=
            {
                "main.pxl": """import "std/text.pxl" as text
effect fn main() { print(text.Lines(input())) }"""
            }
        )
        compiled = compiled["bytecode"]
        for value, expected in [
            ("", [""]),
            ("a\r\nb\rc\n", ["a", "b", "c", ""]),
            ("日志\n  \n末尾", ["日志", "  ", "末尾"]),
            ("\r\n\n", ["", "", ""]),
        ]:
            with self.subTest(value=value):
                self.assertEqual(
                    VM(compiled, input_text=value).run(), [expected]
                )
