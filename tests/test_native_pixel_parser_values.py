"""Owned pixel-parser state preserves syntax, physical origins and diagnostics."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from pixellang.code_image import CodeImage
from pixellang.compiler_service import CompilerService
from pixellang.codec import rgba
from pixellang.vm import VM
from tests.native_compiler import compile_native_sources

ROOT = Path(__file__).resolve().parents[1]
MODULES = ('model.pxl', 'spatial_layout.pxl', 'pixel_tokens.pxl', 'pixel_decode.pxl',
           'pixel_regions.pxl', 'pixel_reader.pxl', 'pixel_types.pxl',
           'pixel_expressions.pxl', 'pixel_statements.pxl', 'pixel_restore.pxl', 'failures.pxl')
DRIVER = '''import "pixel_decode.pxl" as decoder
import "pixel_regions.pxl" as regions
import "pixel_statements.pxl" as statements
import "spatial_layout.pxl" as layout
effect fn main() {
 match jsonDecode::[layout.Source](input()) {
  .Ok(document)=>{print(jsonFrom(statements.Parse(regions.Analyze(decoder.Decode(document,"fixture.pixel"),document.spatial.links,"fixture.pixel"),"fixture.pixel",0)))},
  .Err(error)=>{print(error)}
 }
}
'''


class NativePixelParserValueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files = {name: (ROOT / 'selfhost' / name).read_text() for name in MODULES}
        cls.image = CodeImage(compile_native_sources({**files, 'main.pxl': DRIVER}))
        cls.reference = CodeImage(json.loads((ROOT / 'pixellang/artifacts/compiler.json').read_text()))
        cls.service = CompilerService(cls.reference)
        cls.addClassCleanup(cls.service.close)

    def document(self, source):
        result = self.service.request(dict(version=1, operation='pixels', files={'main.pxl': source}))
        self.assertEqual(result['status'], 'ok', result)
        document = deepcopy(result['result']['document'])
        if 'bundle' in document:
            self.assertEqual(document.pop('bundle')['modules'], {})
        return document

    def compare(self, document):
        def run(code, request):
            output = VM(code, input_text=json.dumps(request), max_steps=30_000_000,
                        max_heap_items=16_000_000, max_output_chars=16_000_000).run()[0]
            return json.loads(output) if isinstance(output, str) else output
        actual = run(self.image, document)
        expected = run(self.reference, dict(operation='pixel-ast', source='fixture.pixel', text='', document=document))
        self.assertEqual(actual, expected)
        return actual

    def test_nested_values_functions_and_control_flow_keep_full_ast(self):
        sources = [
            'record Box[T] {value:T}\nfn copy[T](x:T)->Box[T]=Box::[T]{value:x}\neffect fn main(){var a=[copy(1)];a.append(copy(2));print(a)}',
            'fn edit(x:inout [int]){x.append(1)}\neffect fn main(){var a=[1];edit(&a);let f=fn(x:int)=x+1;print(f(a[0]))}',
            'enum State{Ready,Done(value:int)}\neffect fn main(){let s=State.Done(2);print(match s {State.Ready=>0,State.Done(x)=>x});match s {State.Ready=>{},State.Done(x)=>{print(x)}}}',
            'record R{items:[int],size:int}\neffect fn main(){let r=R{items:[1,2],size:2};let s=r with {items:[r.items[1]],size:1};print(s)}',
            'effect fn main(){for i in [1,2]{if i>0{continue}else{break}};var n=0;while n<2{n+=1};match parseInt("bad"){.Ok(value)=>{print(value)},.Err(error)=>{print(error.code)}}}',
            'effect fn main(){let m=map[string,[int]]{"界😀":[1,2],"b":[]};print(m);print(-9223372036854775808);print(1.25)}',
        ]
        for source in sources:
            with self.subTest(source=source):
                result = self.compare(self.document(source))
                self.assertEqual(result['diagnostics'], [])
                self.assertEqual(len(result['entries']), 1)

    def test_spatial_and_nested_flow_declarations_keep_origins(self):
        source = '''frame World = meters
fn Add(previous:int,value:int)->int=previous+value
flow Child(seed:int) step(value:int)->int {
 state old:int=seed
 let sum=Add(old,value)
 next old=sum
 output old
}
flow Parent(seed:int) step(value:int)->int {
 instance child=Child(seed)
 output child.step(value)
}
effect fn main(){let instance=Parent(1);print(instance.step(2))}
'''
        result = self.compare(self.document(source))
        self.assertEqual(result['diagnostics'], [])
        kinds = {node['kind'] for node in result['syntax']['nodes']}
        self.assertTrue({'frame', 'flow', 'flow_instance', 'flow_state', 'flow_next', 'initial_parameter'} <= kinds)

    def test_invalid_encoding_discards_partial_syntax(self):
        document = self.document('effect fn main(){print(1)}')
        document['pixels'][0]['rgba'] = [255, 0, 0, 255]
        result = self.compare(document)
        self.assertTrue(result['diagnostics'])
        self.assertEqual(result['entries'], [])
        self.assertEqual(result['syntax']['roots'], [])

    def test_bad_header_retains_physical_diagnostic_position(self):
        document = self.document('effect fn main(){print(1)}')
        closing = next(pixel for pixel in document['pixels'] if pixel['rgba'] == rgba('punct', ')'))
        closing['rgba'] = rgba('punct', ']')
        result = self.compare(document)
        self.assertTrue(result['diagnostics'])
        self.assertTrue(any(item['position'] == closing['position'] for item in result['diagnostics']))
