import unittest

from pixellang.canonical import encode
from pixellang.compiler import frontend
from pixellang.model import PixelError
from pixellang.picture import decode_picture, encode_picture
from pixellang.text import TextParser, parse_text
from pixellang.typeexpr import parse_type


class FunctionTypeSyntaxTests(unittest.TestCase):
    def test_text_display_and_pixel_type_roundtrip(self):
        for authoring, internal in [
            ("fn()", "fn()->(unit)"),
            ("fn(int, string) -> bool", "fn(int,str)->(bool)"),
            ("[fn(int) -> string]", "fn(int)->(str)[]"),
            ("fn(int) -> [string]", "fn(int)->(str[])"),
            ("fn(fn(int) -> bool) -> fn()", "fn(fn(int)->(bool))->(fn()->(unit))"),
            ("map[fn() -> Option[int]]", "fn()->(Option<int>){}"),
        ]:
            with self.subTest(authoring=authoring):
                parser = TextParser(authoring, "type.pxl")
                self.assertEqual(parser.typename(), internal)
                self.assertEqual(parse_type(internal).display(), authoring)
                tree = parse_text("fn accept(callback: " + authoring + ") {}")
                tree.data["body"][0].data["name"] = 1
                tree.data["body"][0].data["params"][0]["name"] = 2
                image = decode_picture(encode_picture(encode(tree)))
                recovered, _ = frontend(image)
                self.assertEqual(
                    recovered.data["body"][0].data["params"][0]["type"], internal
                )

    def test_invalid_and_excessive_function_types_rejected(self):
        for text in [
            "fn(int,) -> bool",
            "fn( -> int",
            "fn() ->",
            "fn(" * 34 + "int" + ")" * 34,
        ]:
            with self.subTest(text=text), self.assertRaises(PixelError):
                TextParser(text, "type.pxl").typename()


if __name__ == "__main__":
    unittest.main()
