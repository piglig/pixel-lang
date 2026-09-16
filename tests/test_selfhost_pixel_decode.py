import json
import math
import unittest
from pathlib import Path
from unittest.mock import patch

from pixellang.codec import TABLES, decode_pixel, encode_token, new_source
from pixellang.bootstrap import compile_with
from pixellang.project import compile_project
from pixellang.vm import VM


class PixelDecodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        files = {p.name: p.read_text() for p in (root / 'selfhost').glob('*.pxl')}
        files['main.pxl'] = '''import "pixel_decode.pxl" as decoder
import "spatial_layout.pxl" as layout
record Request { source: layout.Source, literals: bool }
record Output { decoded: decoder.Output, literals: [decoder.Literal] }
fn main() {
    let request = jsonDecode[Request](input())
    let decoded = decoder.Decode(request.source, "fixture.pixel")
    let output = Output{decoded, literals: []}
    if request.literals && len(decoded.diagnostics) == 0 {
        var index = 0
        while index < len(decoded.tokens) {
            let literal = decoder.ReadLiteral(decoded.tokens, index, "fixture.pixel")
            append(output.literals, literal)
            if len(literal.diagnostics) > 0 { break }
            index = literal.next
        }
    }
    print(jsonFrom(output))
}'''
        cls.files = files
        cls.compiler = compile_project(files)[1].bytecode

    def decode(self, colors=None, *, source=None, literals=False):
        if source is None:
            source = new_source(max(1, len(colors)), 1, [
                {'position': [i, 0], 'rgba': color} for i, color in enumerate(colors)
            ])
        vm = VM(self.compiler, input_text=json.dumps(dict(source=source, literals=literals)),
                max_steps=100_000_000)
        with patch('pixellang.codec.decode_pixel', side_effect=AssertionError('host decode')):
            return json.loads(vm.run()[0])

    def test_every_token_table_and_empty_pixels(self):
        colors = [[16, 128, 0, 255], [16, 127, 255, 255], [17, 0, 0, 255],
                  [17, 0, 1, 255], [32, 255, 255, 255], [112, 255, 255, 255],
                  [18, 0, 0, 255], [19, 0, 0, 255], [20, 0, 0, 255]]
        for category, (_, names) in TABLES.items():
            colors.extend([category, i // 256, i % 256, 255] for i in range(len(names)))
        colors.append([255, 123, 42, 0])
        result = self.decode(colors)['decoded']
        self.assertEqual(result['diagnostics'], [])
        expected = []
        for i, color in enumerate(colors):
            token = decode_pixel(color, [i, 0], 'fixture.pixel')
            if token is not None:
                expected.append(dict(kind=token.kind, value=token.value, position=[i, 0]))
        self.assertEqual(result['tokens'], expected)

    def test_literal_round_trip_and_signed_boundaries(self):
        values = [('int', v) for v in (-2**63, -32769, -32768, -1, 0, 32767, 32768, 2**63-1)]
        values += [('bool', False), ('bool', True), ('str', ''), ('str', 'a界😀'),
                   ('float64', -0.0), ('float64', 1.5), ('float64', 5e-324)]
        colors = [color for kind, value in values for color in encode_token(kind, value)]
        result = self.decode(colors, literals=True)
        self.assertEqual(result['decoded']['diagnostics'], [])
        self.assertEqual([(item['kind'], item['value']) for item in result['literals']], values)
        self.assertTrue(all(not item['diagnostics'] for item in result['literals']))
        self.assertEqual(result['literals'][-1]['next'], len(colors))
        self.assertEqual(math.copysign(1, result['literals'][-3]['value']), -1)

    def test_invalid_color_and_geometry_reports_position(self):
        for color in ([1, 0, 0, 255], [17, 0, 2, 255], [48, 255, 255, 255],
                      [16, 0, 0, 1], [16, 0, -1, 255], [16, 0, 0]):
            result = self.decode([color])['decoded']
            self.assertEqual(result['tokens'], [])
            self.assertEqual(result['diagnostics'][0]['position'], [0, 0])
        duplicate = new_source(1, 1, [{'position': [0, 0], 'rgba': [0, 0, 0, 0]}] * 2)
        self.assertIn('Duplicate', self.decode(source=duplicate)['decoded']['diagnostics'][0]['message'])
        outside = new_source(1, 1, [{'position': [1, 0], 'rgba': [16, 0, 0, 255]}])
        self.assertEqual(self.decode(source=outside)['decoded']['diagnostics'][0]['position'], [1, 0])

    def test_invalid_literal_words_padding_utf8_and_float(self):
        cases = [
            [[18, 0, 0, 255]],
            [[18, 0, 1, 255]],
            [[19, 0, 2, 255], [16, 0, 0, 255]],
            [[19, 0, 1, 255], [112, 65, 1, 255]],
            [[19, 0, 1, 255], [112, 255, 0, 255]],
            [[20, 0, 0, 255], [112, 127, 240, 255]] + [[112, 0, 0, 255]] * 3,
        ]
        for colors in cases:
            with self.subTest(colors=colors):
                result = self.decode(colors, literals=True)
                self.assertEqual(result['decoded']['diagnostics'], [])
                self.assertTrue(result['literals'][0]['diagnostics'])
                self.assertEqual(result['literals'][0]['next'], 0)

    def test_rejects_invalid_dimensions_and_version_axes(self):
        for dims in ([0, 1], [1, 0], [1000000, 2], [1], [1, 1, 1]):
            source = new_source(1, 1)
            source['dimensions'] = dims
            result = self.decode(source=source)['decoded']
            self.assertTrue(result['diagnostics'])
            self.assertEqual(result['tokens'], [])
        for versions in ({}, {'language': '0.7', 'encoding': '0.8', 'format': '0.8'},
                         {'language': '0.8', 'encoding': '0.8', 'format': '0.8', 'extra': '0.8'}):
            source = new_source(1, 1)
            source['versions'] = versions
            self.assertTrue(self.decode(source=source)['decoded']['diagnostics'])

    def test_decoder_compiles_with_pixellang_frontend_and_assembler(self):
        root = Path(__file__).resolve().parents[1]
        seed = compile_project({p.name: p.read_text() for p in (root / 'selfhost').glob('*.pxl')})[1].bytecode
        generated, _ = compile_with(seed, self.files)
        colors = encode_token('int', -2**63) + encode_token('str', '界😀') + encode_token('float64', -0.0)
        source = new_source(len(colors), 1, [dict(position=[i, 0], rgba=color) for i, color in enumerate(colors)])
        request = json.dumps(dict(source=source, literals=True))
        with patch('pixellang.codec.decode_pixel', side_effect=AssertionError('host decode')):
            actual = json.loads(VM(generated, input_text=request, max_steps=1_000_000).run()[0])
        self.assertEqual(actual, self.decode(colors, literals=True))
