"""Value-based formatter preserves AST, trivia and executable semantics."""
import json
from pathlib import Path
import unittest

from pixellang.code_image import CodeImage
from pixellang.compiler_service import CompilerService
from pixellang.vm import VM
from tests.native_compiler import compile_native_sources

ROOT=Path(__file__).resolve().parents[1]
DRIVER='''import "source_format.pxl" as formatter
record Request {source:string,text:string}
effect fn main() {
 match jsonDecode::[Request](input()) {
  .Ok(request) => { print(formatter.Format(request.source,request.text)) },
  .Err(error) => { print(error.message) }
 }
}'''


class NativeFormatValueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files={name:(ROOT/'selfhost'/name).read_text() for name in
               ('model.pxl','lexer.pxl','parser.pxl','source_format.pxl')}
        cls.image=CodeImage(compile_native_sources({**files,'main.pxl':DRIVER}))
        cls.reference=CompilerService(json.loads((ROOT/'pixellang/artifacts/compiler.json').read_text()))
        cls.addClassCleanup(cls.reference.close)

    def format(self,source):
        return VM(self.image,input_text=json.dumps(dict(source='main.pxl',text=source)),
                  max_steps=20_000_000,max_output_chars=16_000_000).run()[0]

    def test_comments_literals_value_writes_and_execution(self):
        source='''// 世界: { }
fn add(values:inout [int]){values.append(2)}
effect fn main(){
/* untouched
 spacing [ */ var values=[1]
add(&values)
let identity=fn(value:int)->int=value
print(identity(values[0])+values[1]) // keep
print("}\\n")
}'''
        formatted=self.format(source)
        self.assertEqual(formatted['diagnostics'],[])
        text=formatted['text']
        self.assertEqual(self.format(text),formatted)
        self.assertIn('/* untouched\n spacing [ */',text)
        self.assertIn('add(&values)',text)
        self.assertIn('// keep',text)
        expected=self.reference.request(dict(version=1,operation='format',files={'main.pxl':source}))
        self.assertEqual(expected['status'],'ok',expected)
        self.assertEqual(text,expected['result']['text'])
        for program in (source,text):
            code=self.reference.request(dict(version=1,operation='compile',files={'main.pxl':program}))
            self.assertEqual(code['status'],'ok',code)
            self.assertEqual(VM(code['result']['bytecode']).run(),[3,'}\n'])

    def test_empty_comments_crlf_and_unavailable_import(self):
        for source in ('','// comment','import "absent.pxl" as lib\r\nfn main(){\r\nlet a=[\r\n1,2\r\n]\r\nlib.use(a)\r\n}\r\n'):
            with self.subTest(source=source):
                actual=self.format(source)
                self.assertEqual(actual['diagnostics'],[])
                self.assertEqual(self.format(actual['text']),actual)
                expected=self.reference.request(dict(version=1,operation='format',files={'main.pxl':source}))
                self.assertEqual(expected['status'],'ok',expected)
                self.assertEqual(actual['text'],expected['result']['text'])

    def test_invalid_program_preserves_text_and_diagnostic_location(self):
        source='fn main(){\nlet = 1\n}'
        result=self.format(source)
        self.assertEqual(result['text'],source)
        self.assertTrue(result['diagnostics'])
        self.assertEqual(result['diagnostics'][0]['source'],'main.pxl')
        self.assertEqual(result['diagnostics'][0]['line'],2)
