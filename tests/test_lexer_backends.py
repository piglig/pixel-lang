"""Compare the isolated lexer, complete compiler and native/VM execution."""
import json
import unittest
from pathlib import Path
from pixellang.code_image import CodeImage
from pixellang.vm import VM
from tests import test_selfhost_lexer as lexer_tests
from tests.native_compiler import compile_native_sources, native_driver
from tests.selfhost_runtime import SELFHOST_MAX_OUTPUT_CHARS

ROOT = Path(__file__).resolve().parents[1]
DRIVER = '''import "lexer.pxl" as lexer
record Request { source: string, text: string }
effect fn main() {
 match jsonDecode::[Request](input()) { .Ok(request)=>{Dispatch(request)}, .Err(error)=>{print(error.message)} }
}
effect fn Dispatch(request:Request) {
 print(lexer.Scan(request.source,request.text))
}'''


class LexerBackendTests(unittest.TestCase):
    scan = lexer_tests.SelfHostedLexerTests.scan

    @classmethod
    def setUpClass(cls):
        cls.files = {p.name:p.read_text() for p in (ROOT/'selfhost').glob('*.pxl')}
        cls.reference = CodeImage(json.loads((ROOT/'pixellang/artifacts/compiler.json').read_text()))
        cls.image = CodeImage(compile_native_sources({
            'model.pxl':cls.files['model.pxl'], 'lexer.pxl':cls.files['lexer.pxl'], 'main.pxl':DRIVER}))

    def test_isolated_lexer_matches_complete_compiler_on_sources(self):
        # Golden token and error tests live in test_selfhost_lexer. This checks
        # that compiling the component in isolation preserves the public result.
        with native_driver(self.image) as scan, native_driver(self.reference) as reference:
            for name, text in self.files.items():
                with self.subTest(source=name):
                    request=dict(source=name,text=text)
                    expected=reference(request,steps=20_000_000,output_chars=SELFHOST_MAX_OUTPUT_CHARS)[0]
                    self.assertEqual(scan(request,steps=20_000_000,
                        output_chars=SELFHOST_MAX_OUTPUT_CHARS)[0], expected)

    def test_native_component_matches_reference_vm(self):
        with native_driver(self.image) as scan:
            for text in ('let 数量=12\n"hello"','"unterminated'):
                with self.subTest(text=text):
                    self.assertEqual(scan(dict(source='test.pxl',text=text),steps=20_000_000,
                        output_chars=SELFHOST_MAX_OUTPUT_CHARS)[0],self.scan(text))
