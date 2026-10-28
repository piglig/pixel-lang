import unittest

from pixellang.compiler_client import CompilerClient
from pixellang.model import PixelError
from pixellang.typeexpr import parse_type
from pixellang.vm import VM


class FunctionTypeSyntaxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def test_text_display_and_pixel_type_roundtrip(self):
        for authoring, internal in [
            ("fn()", "fn()->(unit)"),
            ("fn(int, string) -> bool", "fn(int,str)->(bool)"),
            ("[fn(int) -> string]", "fn(int)->(str)[]"),
            ("fn(int) -> [string]", "fn(int)->(str[])"),
            ("fn(fn(int) -> bool) -> fn()", "fn(fn(int)->(bool))->(fn()->(unit))"),
            ("map[string, fn() -> Option[int]]", "map<str,fn()->(Option<int>)>"),
        ]:
            with self.subTest(authoring=authoring):
                files = {'main.pxl': 'fn accept(callback: ' + authoring + ') {}\nfn main(){let reference=accept}'}
                compiled = self.compiler.request('compile', files=files)['result']['bytecode']
                self.assertIn([internal], [f['params'] for f in compiled['functions'].values()])
                self.assertEqual(parse_type(internal).display(), authoring)
                image = self.compiler.request('export-png', files=files)['result']['image']
                recovered = self.compiler.request('compile-png', image=image)['result']['bytecode']
                self.assertIn([internal], [f['params'] for f in recovered['functions'].values()])
                self.assertEqual(VM(recovered).run(), [])

    def test_function_type_accepts_trailing_comma(self):
        code = self.compiler.request('compile', files={'main.pxl': 'fn accept(value:fn(int,) -> bool){}\nfn main(){let reference=accept}'})['result']['bytecode']
        self.assertIn(['fn(int)->(bool)'], [f['params'] for f in code['functions'].values()])

    def test_invalid_and_excessive_function_types_rejected(self):
        for text in ["fn( -> int", "fn() ->"]:
            with self.subTest(text=text), self.assertRaises(PixelError):
                self.compiler.request('compile', files={'main.pxl': 'fn accept(value:' + text + '){}\nfn main(){}'})
        with self.assertRaisesRegex(PixelError, 'Type nesting exceeds 128'):
            self.compiler.request('compile', files={'main.pxl': 'fn accept(value:' + 'fn(' * 129 + 'int' + ')' * 129 + '){}\nfn main(){}'})


if __name__ == '__main__':
    unittest.main()
