import json
import unittest
from pathlib import Path
from unittest.mock import patch

from pixellang.builder import token
from pixellang.codec import encode_token, validate
from pixellang.compiler import compile_source
from pixellang.model import PixelError
from pixellang.project import compile_project
from pixellang.spatial import analyze
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
        files = {p.name: p.read_text() for p in (ROOT / 'selfhost').glob('*.pxl')}
        files['main.pxl'] = '''import "spatial_layout.pxl" as layout
record Request { regions: [layout.Region], spacing: int, reverse: bool }
fn main() {
 let request = jsonDecode[Request](input())
 print(layout.Build(request.regions, request.spacing, request.reverse))
}
'''
        cls.bytecode = compile_project(files)[1].bytecode

    def layout(self, regions, spacing=1, reverse=False):
        vm = VM(self.bytecode, input_text=json.dumps({'regions': regions, 'spacing': spacing, 'reverse': reverse}), max_steps=10_000_000)
        with patch('pixellang.builder.Grid.build', side_effect=AssertionError('delegated')), patch(
            'pixellang.spatial.analyze', side_effect=AssertionError('delegated')
        ):
            return vm.run()[0]

    def test_program_execution_survives_spacing_and_reverse_order(self):
        regions = [region(-1, 'entry', 'fn', ('id', 99), '(', ')', '->', 'unit'),
                   region(0, 'print', 9223372036854775807),
                   region(0, 'print', ('str', '界😀')),
                   region(0, 'print', 10, '-', 3)]
        for spacing, reverse in ((1, False), (7, False), (7, True)):
            with self.subTest(spacing=spacing, reverse=reverse):
                doc = self.layout(regions, spacing, reverse)
                self.assertEqual(VM(compile_source(doc).bytecode).run(), [9223372036854775807, '界😀', 7])
                self.assertEqual(doc['metadata'], {})
                self.assertTrue(all(p['rgba'][3] == 255 for p in doc['pixels']))

    def test_parent_overrides_indentation_inference(self):
        # Region 3 shares region 1's depth, but is explicitly a child of region 0.
        rows = [region(-1, 'fn', ('id', 1), '(', ')', '->', 'unit'),
                region(0, 'if', True), region(1, 'print', 1),
                region(0, 'print', 2), region(-1, 'entry', 'fn', ('id', 99), '(', ')', '->', 'unit'),
                region(4, 'do', ('id', 1), '(', ')')]
        doc = self.layout(rows, 5, True)
        tokens = validate(doc)
        parsed = analyze(tokens, doc['spatial']['links'])
        self.assertEqual([r.parent for r in parsed], [None, 0, 1, 0, None, 4])
        self.assertEqual(VM(compile_source(doc).bytecode).run(), [1, 2])

    def test_explicit_parent_can_skip_the_nearest_open_region(self):
        doc = self.layout([region(-1, 'fn'), region(-1, 'fn'), region(0, 'return')])
        parsed = analyze(validate(doc), doc['spatial']['links'])
        self.assertEqual([r.parent for r in parsed], [None, None, 0])

    def test_layout_components_compile_with_pixellang(self):
        from tests.test_selfhost_ir import SelfHostedIRTests
        SelfHostedIRTests.setUpClass()
        files = {name: (ROOT / 'selfhost' / name).read_text() for name in (
            'spatial_layout.pxl', 'pixel_tokens.pxl', 'literal_pixels.pxl')}
        files['main.pxl'] = '''import "spatial_layout.pxl" as layout
import "pixel_tokens.pxl" as tokens
import "literal_pixels.pxl" as literals
fn main() {
 let header = [tokens.Symbol(64, "entry"), tokens.Symbol(64, "fn"), tokens.Identifier(7), tokens.Symbol(80, "("), tokens.Symbol(80, ")"), tokens.Symbol(80, "->"), tokens.Symbol(96, "unit")]
 let body = [tokens.Symbol(64, "print")]
 for color in literals.Integer(42) { append(body, color) }
 print(layout.Build([layout.Region{colors: header, parent: -1}, layout.Region{colors: body, parent: 0}], 9, true))
}
'''
        result = SelfHostedIRTests().lower(files, 'bytecode')
        self.assertEqual(result['diagnostics'], [])
        doc = VM(result['bytecode'], max_steps=1_000_000).run()[0]
        self.assertEqual(VM(compile_source(doc).bytecode).run(), [42])

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
            with self.subTest(message=message), self.assertRaisesRegex(PixelError, message):
                self.layout(rows, spacing)
