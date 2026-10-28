import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.native_compiler import compile_native_sources
from pixellang.builder import token
from pixellang.codec import encode_token, validate
from pixellang.compiler_client import CompilerClient
from pixellang.model import PixelError
from pixellang.vm import VM

ROOT = Path(__file__).resolve().parents[1]


def region(parent, *values):
    colors = []
    for value in values:
        colors.extend(encode_token(*token(value)))
    return {'parent': parent, 'colors': colors}


class SelfHostedSpatialLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = CompilerClient()
        cls.addClassCleanup(cls.client.close)
        files = {name: (ROOT / 'selfhost' / name).read_text() for name in (
            'spatial_layout.pxl', 'pixel_decode.pxl', 'pixel_tokens.pxl', 'pixel_regions.pxl', 'failures.pxl')}
        files['main.pxl'] = '''import "spatial_layout.pxl" as layout
import "pixel_decode.pxl" as decoder
import "pixel_regions.pxl" as regions
record Request { regions: [layout.Region], spacing: int, reverse: bool, document: Option[layout.Source] }
effect fn main() {
 match jsonDecode::[Request](input()) { .Ok(request) => Dispatch(request), .Err(error) => print(error) }
}
effect fn Dispatch(request: Request) {
 match request.document {
  Option.Some(document) => { print(regions.Analyze(decoder.Decode(document, "test"), document.spatial.links, "test")); return }
  Option.None => {}
 }
 match layout.Build(request.regions, request.spacing, request.reverse) { .Ok(source) => print(source), .Err(error) => print(error) }
}
'''
        cls.bytecode = compile_native_sources(files)

    def layout(self, regions, spacing=1, reverse=False):
        vm = VM(self.bytecode, input_text=json.dumps({'regions': regions, 'spacing': spacing, 'reverse': reverse}), max_steps=10_000_000)
        with patch('pixellang.builder.Grid.build', side_effect=AssertionError('delegated')), patch(
            'pixellang.spatial.analyze', side_effect=AssertionError('delegated')
        ):
            return vm.run()[0]

    def parents(self, document):
        output = VM(self.bytecode, input_text=json.dumps(dict(
            regions=[], spacing=1, reverse=False, document=document)),
            max_steps=10_000_000).run()[0]
        self.assertEqual(output['diagnostics'], [])
        return [region['parent'] for region in output['regions']]

    def test_program_execution_survives_spacing_and_reverse_order(self):
        regions = [region(-1, 'entry', 'effect_fn', ('id', 99), '(', ')', '->', 'unit'),
                   region(0, 'print', 9223372036854775807),
                   region(0, 'print', ('str', '界😀')),
                   region(0, 'print', 10, '-', 3)]
        for spacing, reverse in ((1, False), (7, False), (7, True)):
            with self.subTest(spacing=spacing, reverse=reverse):
                doc = self.layout(regions, spacing, reverse)
                self.assertEqual(VM(self.client.request('compile-pixels', document=doc)['result']['bytecode']).run(), [9223372036854775807, '界😀', 7])
                self.assertEqual(doc['metadata'], {})
                self.assertTrue(all(p['rgba'][3] == 255 for p in doc['pixels']))

    def test_parent_links_preserve_sibling_scope(self):
        # Region 3 shares region 1's depth, but is explicitly a child of region 0.
        rows = [region(-1, 'effect_fn', ('id', 1), '(', ')', '->', 'unit'),
                region(0, 'if', True), region(1, 'print', 1),
                region(0, 'print', 2), region(-1, 'entry', 'effect_fn', ('id', 99), '(', ')', '->', 'unit'),
                region(4, 'do', ('id', 1), '(', ')')]
        doc = self.layout(rows, 5, True)
        self.assertEqual(self.parents(doc), [-1, 0, 1, 0, -1, 4])
        self.assertEqual(VM(self.client.request('compile-pixels', document=doc)['result']['bytecode']).run(), [1, 2])

    def test_explicit_parent_can_skip_the_nearest_open_region(self):
        doc = self.layout([region(-1, 'fn'), region(-1, 'fn'), region(0, 'return')])
        self.assertEqual(self.parents(doc), [-1, -1, 0])

    def test_layout_components_compile_with_pixellang(self):
        files = {name: (ROOT / 'selfhost' / name).read_text() for name in (
            'spatial_layout.pxl', 'pixel_tokens.pxl', 'literal_pixels.pxl', 'failures.pxl')}
        files['main.pxl'] = '''import "spatial_layout.pxl" as layout
import "pixel_tokens.pxl" as tokens
import "literal_pixels.pxl" as literals
effect fn main() { match Build() { .Ok(value) => {}, .Err(error) => print(error) } }
effect fn Build() -> Result[int, Error] {
 let indices = tokens.Index()
 let header = [tokens.Symbol(64, "entry", indices)?, tokens.Symbol(64, "effect_fn", indices)?, tokens.Identifier(7)?, tokens.Symbol(80, "(", indices)?, tokens.Symbol(80, ")", indices)?, tokens.Symbol(80, "->", indices)?, tokens.Symbol(96, "unit", indices)?]
 var body: [[int]] = [tokens.Symbol(64, "print", indices)?]
 for color in literals.Integer(42) { body.append(color) }
 match layout.Build([layout.Region{colors: header, parent: -1}, layout.Region{colors: body, parent: 0}], 9, true) { .Ok(source) => print(source), .Err(error) => print(error) }
 return .Ok(0)
}
'''
        result = self.client.request('compile', files=files)['result']
        doc = VM(result['bytecode'], max_steps=1_000_000).run()[0]
        self.assertEqual(VM(self.client.request('compile-pixels', document=doc)['result']['bytecode']).run(), [42])

    def test_empty_source_is_structurally_valid(self):
        doc = self.layout([])
        self.assertEqual(doc['dimensions'], [1, 1])
        self.assertEqual(validate(doc), [])

    def test_invalid_geometry_and_colors(self):
        cases = [([region(0, 'print')], 1, 'earlier region'),
                 ([region(-2, 'print')], 1, 'earlier region'),
                 ([{'parent': -1, 'colors': []}], 1, 'at least one'),
                 ([region(-1, 'print')], 0, 'spacing'),
                 ([{'parent': -1, 'colors': [[17, 0, 1]]}], 1, 'four bytes'),
                 ([{'parent': -1, 'colors': [[17, 0, 256, 255]]}], 1, 'must be bytes'),
                 ([{'parent': -1, 'colors': [[17, 0, 1, 0]]}], 1, 'opaque'),
                 ([{'parent': -1, 'colors': [[17, 0, 1, 255]] * 4000}], 256, 'one million')]
        for rows, spacing, message in cases:
            with self.subTest(message=message):
                error = self.layout(rows, spacing)
                self.assertEqual(error['kind'], 'compiler')
                self.assertEqual(error['code'], 'compiler.invalid_data')
                self.assertIn(message, error['message'])
