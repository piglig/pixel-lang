import json
import unittest
from pathlib import Path

from pixellang.codec import encode_token, new_source
from pixellang.project import compile_project
from pixellang.vm import VM


class PixelTypeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        files = {p.name: p.read_text() for p in (root / 'selfhost').glob('*.pxl')}
        files['main.pxl'] = '''import "pixel_decode.pxl" as decoder
import "pixel_reader.pxl" as reader
import "pixel_types.pxl" as types
import "spatial_layout.pxl" as layout
import "model.pxl" as model
record Output { root: int, nodes: [model.Node], diagnostics: [decoder.Diagnostic] }
fn main() {
 let source = jsonDecode[layout.Source](input())
 let decoded = decoder.Decode(source, "fixture.pixel")
 let r = reader.New("fixture.pixel", 0, decoded.tokens)
 let root = types.Parse(r)
 if r.index != len(r.tokens) { reader.Problem(r, "Unexpected trailing token") }
 print(jsonFrom(Output{root, nodes: r.nodes, diagnostics: r.diagnostics}))
}'''
        cls.files = files
        cls.compiler = compile_project(files)[1].bytecode

    def source(self, values):
        punctuation = {'(', ')', '[', ']', ',', '.', '->'}
        colors = [color for value in values for color in encode_token(
            'id' if type(value) is int else ('punct' if value in punctuation else 'type'), value)]
        return new_source(max(1, len(colors)), 1, [dict(position=[i, 0], rgba=c) for i, c in enumerate(colors)])

    def parse(self, values):
        return json.loads(VM(self.compiler, input_text=json.dumps(self.source(values)), max_steps=1_000_000).run()[0])

    def tree(self, result, index=None):
        node = result['nodes'][result['root'] if index is None else index]
        return (node['kind'], node['text'], [self.tree(result, child) for child in node['children']])

    def test_primitives_and_generic_nominal_identity(self):
        for name in ('int', 'bool', 'str', 'unit', 'json', 'Error', 'float64'):
            result = self.parse([name])
            self.assertEqual(result['diagnostics'], [])
            self.assertEqual(self.tree(result), ('type', 'string' if name == 'str' else name, []))
        result = self.parse(['record', '(', 1, '.', 7, '[', 'parameter', '(', 99, ')', ']', ')'])
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(self.tree(result), ('type', '_module_1._pixel_7', [('type', '_pixel_99', [])]))
        self.assertEqual(self.tree(self.parse(['record', '(', 0, '.', 7, ')'])), ('type', '_pixel_7', []))

    def test_nested_function_collection_types(self):
        result = self.parse(['function', '(', 'map', '(', 'int', ')', '[', ']', ',',
                             'Option', '(', 'str', ')', ')', '->', '(', 'bool', ')', '[', ']'])
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(self.tree(result), ('type_array', '', [('type_fn', '', [
            ('type_array', '', [('type', 'map', [('type', 'int', [])])]),
            ('type', 'Option', [('type', 'string', [])]), ('type', 'bool', [])])]))

    def test_malformed_and_excessive_depth(self):
        cases = [[], ['map', '('], ['record', '(', 'int', '.', 1, ')'],
                 ['parameter', '(', ')'], ['function', '(', ')', '->', 'int'],
                 ['int', '[', 'bool', ']'], ['int', 'bool'],
                 ['int'] + ['[', ']'] * 32]
        for values in cases:
            with self.subTest(values=values):
                result = self.parse(values)
                self.assertTrue(result['diagnostics'])
                self.assertEqual(result['diagnostics'][0]['source'], 'fixture.pixel')
        self.assertEqual(self.parse(['int'] + ['[', ']'] * 31)['diagnostics'], [])

    def test_type_reader_compiles_with_pixellang(self):
        from pixellang.bootstrap import compile_with
        root = Path(__file__).resolve().parents[1]
        seed = compile_project({p.name: p.read_text() for p in (root / 'selfhost').glob('*.pxl')})[1].bytecode
        generated, _ = compile_with(seed, self.files)
        values = ['function', '(', 'Option', '(', 'str', ')', ')', '->', '(', 'int', ')']
        actual = json.loads(VM(generated, input_text=json.dumps(self.source(values)), max_steps=1_000_000).run()[0])
        self.assertEqual(actual, self.parse(values))
