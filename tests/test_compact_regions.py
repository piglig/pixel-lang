import copy
import json
import unittest
from pathlib import Path

from tests.native_compiler import compile_native_sources
from pixellang.codec import validate, encode_token
from pixellang.compiler_client import CompilerClient
from pixellang.model import PixelError
from pixellang.spatial import analyze
from pixellang.vm import VM


def row(parent, tokens):
    return dict(parent=parent, colors=[color for kind, value in tokens for color in encode_token(kind, value)])


class CompactRegionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = CompilerClient()
        cls.addClassCleanup(cls.client.close)
        root = Path(__file__).resolve().parents[1]
        files = {p.name: p.read_text() for p in (root / 'selfhost').glob('*.pxl')}
        files['main.pxl'] = '''import "spatial_layout.pxl" as layout
import "pixel_decode.pxl" as decode
import "pixel_regions.pxl" as regions
record Request { rows: [layout.Region], document: Option[layout.Source] }
effect fn RunTest()->Result[int,Error] {
 let r = (jsonDecode::[Request](input()))?
 let source = match r.document { Option.Some(value) => value, Option.None => (layout.Compact(r.rows))? }
 print(source)
 print(regions.Analyze(decode.Decode(source, "compact.pixel"), source.spatial.links, "compact.pixel"))

 return .Ok(0)
}
effect fn main(){match RunTest(){.Ok(done)=>{},.Err(error)=>{print(error)}}}
'''
        cls.code = compile_native_sources(files)

    def request(self, rows=None, document=None):
        return VM(self.code, input_text=json.dumps(dict(rows=rows or [], document=document)), max_steps=10000000).run()

    def fixture(self):
        fn = [('keyword', 'effect_fn'), ('id', 1), ('punct', '('), ('punct', ')'), ('punct', '->'), ('type', 'unit')]
        return self.request([
            row(-1, [('keyword', 'entry')] + fn),
            row(0, [('keyword', 'print'), ('int', 42)]),
            row(0, [('keyword', 'print'), ('str', 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ')]),
            row(-1, [('keyword', 'effect_fn'), ('id', 9)] + fn[2:]),
            row(3, [('keyword', 'print'), ('int', 7)]),
        ])

    def test_region_order_parents_and_execution_match_reference(self):
        source, parsed = self.fixture()
        self.assertEqual(parsed['diagnostics'], [])
        native = analyze(validate(source), source['spatial']['links'])
        self.assertEqual([r.parent for r in native], [None, 0, 0, None, 3])
        self.assertEqual([r['parent'] for r in parsed['regions']], [-1, 0, 0, -1, 3])
        heads = [link['from'] for link in source['spatial']['links'] if link['kind'] == 'region']
        self.assertLess(len({p[1] for p in heads}), len(heads))
        code = self.client.request('compile-pixels', document=source)['result']['bytecode']
        self.assertEqual(VM(code).run(), [42, 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'])

    def test_invalid_boundaries_and_chains_are_rejected_by_both_readers(self):
        source, _ = self.fixture()
        markers = [i for i, link in enumerate(source['spatial']['links']) if link['kind'] == 'region']
        variants = []
        missing = copy.deepcopy(source)
        missing['spatial']['links'].pop(markers[1])
        variants.append(missing)
        duplicate = copy.deepcopy(source)
        duplicate['spatial']['links'].append(copy.deepcopy(source['spatial']['links'][markers[0]]))
        variants.append(duplicate)
        broken = copy.deepcopy(source)
        head = source['spatial']['links'][markers[0]]['from']
        broken['spatial']['links'].append(dict(kind='next', **{'from': head, 'to': [head[0] + 1, head[1]]}))
        variants.append(broken)
        wrong = copy.deepcopy(source)
        wrong['spatial']['links'][markers[0]]['to'] = source['spatial']['links'][markers[1]]['from']
        variants.append(wrong)
        for document in variants:
            with self.subTest(links=len(document['spatial']['links'])):
                _, output = self.request(document=document)
                self.assertTrue(output['diagnostics'])
                self.assertFalse(output['regions'])
                with self.assertRaises(PixelError):
                    analyze(validate(document), document['spatial']['links'])

    def test_current_region_openers_agree_with_native_reader(self):
        for opener in ('effect_fn', 'flow', 'lambda_body', 'effect_body', 'value_body'):
            with self.subTest(opener=opener):
                source, parsed = self.request([
                    row(-1, [('keyword', opener)]),
                    row(0, [('keyword', 'print'), ('int', 42)]),
                ])
                self.assertEqual(parsed['diagnostics'], [])
                self.assertEqual([r['parent'] for r in parsed['regions']], [-1, 0])
                self.assertEqual([r.parent for r in analyze(validate(source), source['spatial']['links'])], [None, 0])
