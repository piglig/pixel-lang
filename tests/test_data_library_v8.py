import unittest

from pixellang.compiler import compile_source
from pixellang.picture import decode_picture, encode_picture
from pixellang.project import compile_project
from pixellang.vm import VM


class DataLibraryTests(unittest.TestCase):
    def test_optional_lookup_map_copy_and_histogram(self):
        source, compiled = compile_project(
            {
                "main.pxl": """import "std/maps.pxl" as maps
record Item { value: int }
fn main() {
 let source = map[Item]{"first": Item{value: 1}}
 let copied = maps.Copy(source)
 copied["first"].value = 7
 copied["second"] = Item{value: 9}
 print(source["first"].value)
 print(has(source, "second"))
 print(match maps.Get(source, "absent") { Some(item) => item.value, None => -1 })
 print(match maps.Get(source, "first") { Some(item) => item.value, None => -1 })
 let counts = map[int]{}
 maps.Increment(counts, "b"); maps.Increment(counts, "a"); maps.Increment(counts, "b")
 for key, value in counts { print(key); print(value) }
}"""
            }
        )
        expected = [7, False, -1, 7, "b", 2, "a", 1]
        self.assertEqual(VM(compiled.bytecode).run(), expected)
        self.assertEqual(
            VM(compile_source(decode_picture(encode_picture(source))).bytecode).run(),
            expected,
        )

    def test_physical_lines_preserve_blanks_and_unicode(self):
        _, compiled = compile_project(
            {
                "main.pxl": """import "std/text.pxl" as text
fn main() { print(text.Lines(input())) }"""
            }
        )
        for value, expected in [
            ("", [""]),
            ("a\r\nb\rc\n", ["a", "b", "c", ""]),
            ("日志\n  \n末尾", ["日志", "  ", "末尾"]),
            ("\r\n\n", ["", "", ""]),
        ]:
            with self.subTest(value=value):
                self.assertEqual(
                    VM(compiled.bytecode, input_text=value).run(), [expected]
                )
