import json
import unittest
from pathlib import Path
from unittest.mock import patch

from pixellang.codec import new_source, rgba, validate
from pixellang.spatial import analyze
from pixellang.project import compile_project
from pixellang.vm import VM
from pixellang.bootstrap import compile_with


def document(rows, links=()):
    pixels = [dict(position=[x, y], rgba=rgba(kind, value))
              for y, row in enumerate(rows) for x, kind, value in row]
    return new_source(max((p['position'][0] for p in pixels), default=0) + 1,
                      max(1, len(rows)), pixels, list(links))


def link(kind, a, b):
    return dict(kind=kind, **{'from': list(a), 'to': list(b)})


class PixelRegionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        files = {p.name: p.read_text() for p in (root / 'selfhost').glob('*.pxl')}
        files['main.pxl'] = '''import "pixel_decode.pxl" as decoder
import "pixel_regions.pxl" as regions
import "spatial_layout.pxl" as layout
fn main() {
 let source = jsonDecode[layout.Source](input())
 print(jsonFrom(regions.Analyze(decoder.Decode(source, "fixture.pixel"), source.spatial.links, "fixture.pixel")))
}'''
        cls.files = files
        cls.compiler = compile_project(files)[1].bytecode

    def run_source(self, source):
        with patch('pixellang.spatial.analyze', side_effect=AssertionError('host spatial analysis')):
            return json.loads(VM(self.compiler, input_text=json.dumps(source), max_steps=10_000_000).run()[0])

    def assert_oracle(self, source):
        expected = analyze(validate(source), source['spatial']['links'])
        output = self.run_source(source)
        self.assertEqual(output['diagnostics'], [])
        self.assertEqual(output['regions'], [dict(id=r.id, indent=r.indent,
            parent=-1 if r.parent is None else r.parent, explicitParent=r.explicit_parent,
            tokens=[dict(kind=t.kind, value=t.value, position=list(t.position)) for t in r.tokens]) for r in expected])

    def test_indentation_unsorted_pixels_and_empty_source(self):
        source = document([[(0, 'keyword', 'entry'), (2, 'keyword', 'fn')],
                           [(2, 'keyword', 'if')], [(4, 'keyword', 'print')],
                           [(2, 'keyword', 'return')]])
        source['pixels'].reverse()
        self.assert_oracle(source)
        self.assert_oracle(document([]))

    def test_reversed_explicit_chain_and_non_nearest_parent(self):
        source = document([[(0, 'keyword', 'fn')], [(0, 'keyword', 'fn')],
                           [(2, 'int', 7), (12, 'keyword', 'print')]],
                          [link('next', (12, 2), (2, 2)), link('child', (0, 0), (12, 2))])
        self.assert_oracle(source)

    def test_regions_compile_with_pixellang(self):
        root = Path(__file__).resolve().parents[1]
        seed = compile_project({p.name: p.read_text() for p in (root / 'selfhost').glob('*.pxl')})[1].bytecode
        generated, _ = compile_with(seed, self.files)
        source = document([[(0, 'keyword', 'fn')],
                           [(2, 'int', 7), (12, 'keyword', 'print')]],
                          [link('next', (12, 1), (2, 1)), link('child', (0, 0), (12, 1))])
        with patch('pixellang.spatial.analyze', side_effect=AssertionError('host spatial analysis')):
            actual = json.loads(VM(generated, input_text=json.dumps(source), max_steps=10_000_000).run()[0])
        self.assertEqual(actual, self.run_source(source))

    def test_rejects_invalid_connections_and_parents(self):
        cases = [
            document([[(0, 'keyword', 'fn'), (4, 'id', 1)]]),
            document([[(0, 'keyword', 'fn')]], [link('child', (0, 0), (1, 0))]),
            document([[(0, 'keyword', 'fn')]], [link('next', (0, 0), (0, 0))]),
            document([[(0, 'keyword', 'fn'), (1, 'id', 1), (2, 'id', 2)]],
                     [link('next', (0, 0), (1, 0)), link('next', (0, 0), (2, 0))]),
            document([[(0, 'keyword', 'fn'), (1, 'id', 1), (2, 'id', 2)]], [link('next', (0, 0), (1, 0))]),
            document([[(0, 'keyword', 'fn')], [(2, 'keyword', 'print')]], [link('next', (0, 0), (2, 1))]),
            document([[(0, 'keyword', 'fn')], [(2, 'keyword', 'print')]], [link('child', (2, 1), (0, 0))]),
            document([[(0, 'keyword', 'fn')], [(2, 'keyword', 'print')]], [link('child', (0, 0), (2, 1))] * 2),
            document([[(0, 'keyword', 'print')], [(2, 'keyword', 'print')]]),
        ]
        for source in cases:
            with self.subTest(source=source):
                result = self.run_source(source)
                self.assertTrue(result['diagnostics'])
                self.assertEqual(result['regions'], [])
                self.assertEqual(result['diagnostics'][0]['phase'], 'spatial')
