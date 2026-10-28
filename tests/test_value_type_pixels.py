"""Native type-pixel emission and decoding preserve value types and scoped access without metadata."""
import json
import re
from pathlib import Path
import unittest
from unittest.mock import patch

from pixellang.code_image import CodeImage
from pixellang.codec import encode_token, new_source
from pixellang.picture import encode_picture, decode_picture
from pixellang.vm import VM
from pixellang.model import PixelError
from tests.native_compiler import compile_native_sources
from tests.selfhost_runtime import SELFHOST_HEAP_LIMITS


DRIVER = '''import "model.pxl" as model
import "project.pxl" as project
import "bindings.pxl" as bindings
import "types.pxl" as types
import "pixel_ast_state.pxl" as emitter
import "pixel_decode.pxl" as decoder
import "pixel_reader.pxl" as reader
import "pixel_types.pxl" as pixelTypes
import "pixel_tokens.pxl" as tokens
import "spatial_layout.pxl" as layout
record Request { operation: string, payload: json }
record Decoded { root: int, nodes: [model.Node], diagnostics: [decoder.Diagnostic] }
effect fn RunTest()->Result[int,Error] {
 let request = (jsonDecode::[Request](input()))?
 if request.operation == "decode" {
  let source = (jsonDecode::[layout.Source](request.payload))?
  let decoded = decoder.Decode(source, "type.pixel")
  if len(decoded.diagnostics) > 0 { print(jsonFrom(Decoded{root: -1, nodes: [], diagnostics: decoded.diagnostics})); return .Ok(0) }
  var r = reader.New("type.pixel", 0, decoded.tokens)
  let root = pixelTypes.Parameter(&r)
  if r.index != len(r.tokens) { reader.Problem("Unexpected trailing token", &r) }
  print(jsonFrom(Decoded{root, nodes: r.nodes, diagnostics: r.diagnostics}))
  return .Ok(0)
 }
 let files = (jsonDecode::[map[string,string]](request.payload))?
 let sources = project.Build(files, "main.pxl")
 let resolved = types.Analyze(bindings.Bind(sources))
 assert(len(resolved.diagnostics) == 0)
 let checked: model.ExpressionCheck = model.ExpressionCheck{resolved, calls: [], expressions: [], flows: [], diagnostics: []}
 var symbolIds: map[string, int] = map[string, int]{}
 for index, symbol in resolved.bound.symbols {
  if symbol.module >= 0 { symbolIds[emitter.Key(symbol.module, symbol.node)] = index }
 }
 var c: emitter.Context = emitter.Context{ module: len(sources.modules) - 1, regions: [], origins: [],
  references: map[string, int]{}, declarations: map[string, int]{}, expressions: map[string, int]{},
  calls: map[string, int]{}, annotations: map[string, int]{}, symbolIds: symbolIds, tokenIndices: tokens.Index(),
  bodies: [], imports: map[string, bool]{}, diagnostics: []}
 let signature = resolved.signatures[0]
 let target = resolved.types[signature.typeId].arguments[0]
 var colors: [[int]] = []
 emitter.Type(target, signature.node, checked, &c, &colors)
 assert(len(c.diagnostics) == 0)
 print(jsonFrom(layout.Build([layout.Region{colors: colors, parent: -1}], 1, false)))

 return .Ok(0)
}

effect fn main(){match RunTest(){.Ok(done)=>{},.Err(error)=>{print(error)}}}
'''


class ValueTypePixelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        files = {'main.pxl': DRIVER}
        pending = re.findall(r'import "([^"]+)"', DRIVER)
        while pending:
            name = pending.pop()
            if name in files:
                continue
            files[name] = (root / 'selfhost' / name).read_text()
            pending.extend(re.findall(r'import "([^"]+)"', files[name]))
        cls.image = CodeImage(compile_native_sources(files))

    def request(self, operation, payload):
        with patch('pixellang.text.parse_text', side_effect=AssertionError('Host parser used')):
            output = VM(self.image, **SELFHOST_HEAP_LIMITS, max_steps=5_000_000,
                        input_text=json.dumps(dict(operation=operation, payload=payload))).run()
        self.assertEqual(len(output), 1)
        result = json.loads(output[0])
        if operation == 'emit':
            # layout.Build is fallible; assert success before interpreting its Source.
            self.assertEqual(result['variant'], 'Ok', result)
            return result['fields']['value']
        return result

    def emit(self, typ, declarations=''):
        source = declarations + '\nfn Accept(value: ' + typ + ') {}\neffect fn main() {}'
        document = self.request('emit', {'main.pxl': source})
        self.assertEqual(document['metadata'], {})
        return document

    def tree(self, result, index=None):
        node = result['nodes'][result['root'] if index is None else index]
        return (node['kind'], node['text'], [self.tree(result, child) for child in node['children']])

    def source(self, tokens):
        punctuation = {'(', ')', '[', ']', ',', '.', '->'}
        colors = [color for token in tokens for color in encode_token(
            'punct' if token in punctuation else 'type', token)]
        return new_source(max(1, len(colors)), 1,
                          [dict(position=[i, 0], rgba=color) for i, color in enumerate(colors)])

    def test_native_value_and_access_encoding_matches_independent_tokens(self):
        cases = [
            ('[int]', ['int', '[', ']']),
            ('[[int]]', ['int', '[', ']', '[', ']']),
            ('map[string, [int]]', ['map', '(', 'str', ',', 'int', '[', ']', ')']),
            ('inout [int]', ['inout', '(', 'int', '[', ']', ')']),
            ('fn(inout [int]) -> int',
             ['function', '(', 'inout', '(', 'int', '[', ']', ')', ')', '->', '(', 'int', ')']),
        ]
        for typ, tokens in cases:
            with self.subTest(typ=typ):
                document = self.emit(typ)
                self.assertEqual(document['pixels'], self.source(tokens)['pixels'])
                decoded = self.request('decode', document)
                self.assertEqual(decoded['diagnostics'], [])
                self.assertEqual(sum(n['kind'] == 'type_inout' for n in decoded['nodes']), tokens.count('inout'))

    def test_png_transport_retains_access_without_name_metadata(self):
        document = self.emit('inout map[string, [int]]')
        expected = self.request('decode', document)
        recovered = decode_picture(encode_picture(document, scale=4))
        self.assertEqual(recovered.pop('bundle')['modules'], {})
        recovered['metadata'] = {}
        actual = self.request('decode', recovered)
        self.assertEqual(actual['diagnostics'], [])
        self.assertEqual(self.tree(actual), self.tree(expected))
        self.assertEqual(self.tree(actual)[0], 'type_inout')

    def test_generic_parameter_survives_pixel_and_png_transport(self):
        document = self.request('emit', {'main.pxl': 'fn Accept[T](value: inout T) {}\neffect fn main() {}'})
        decoded = self.request('decode', document)
        self.assertEqual(decoded['diagnostics'], [])
        tree = self.tree(decoded)
        self.assertEqual(tree[0], 'type_inout')
        self.assertTrue(tree[2][0][1].startswith('_pixel_'))
        recovered = decode_picture(encode_picture(document, scale=4))
        self.assertEqual(recovered.pop('bundle')['modules'], {})
        recovered['metadata'] = {}
        after = self.request('decode', recovered)
        self.assertEqual(after['diagnostics'], [])
        self.assertEqual(self.tree(after), tree)

    def test_nominal_generic_identity_remains_inside_access(self):
        document = self.emit('inout Box[[int]]', 'record Box[T] { value: T }')
        decoded = self.request('decode', document)
        self.assertEqual(decoded['diagnostics'], [])
        outer = self.tree(decoded)
        self.assertEqual(outer[0], 'type_inout')
        nominal = outer[2][0]
        self.assertEqual(nominal[0], 'type')
        self.assertTrue(nominal[1].startswith('_pixel_'))
        self.assertEqual(nominal[2][0][0], 'type_array')

    def test_malformed_access_retired_types_and_depth_are_rejected(self):
        for tokens in (['inout'], ['inout', '('], ['inout', '(', ')'],
                       ['inout', '(', 'int', '[', ']'], ['inout', 'int', '[', ']'],
                       ['inout', '(', 'int', ',', 'str', ')']):
            with self.subTest(tokens=tokens):
                self.assertTrue(self.request('decode', self.source(tokens))['diagnostics'])
        for retired in ('mut', 'Frozen'):
            with self.subTest(retired=retired), self.assertRaisesRegex(PixelError, 'Unknown semantic token'):
                encode_token('type', retired)
        at_limit = ['Option', '('] * 31 + ['int'] + [')'] * 31
        too_deep = ['Option', '('] * 32 + ['int'] + [')'] * 32
        self.assertEqual(self.request('decode', self.source(at_limit))['diagnostics'], [])
        self.assertTrue(self.request('decode', self.source(too_deep))['diagnostics'])

    def test_removing_access_token_is_not_silently_accepted(self):
        document = self.emit('inout [int]')
        document['pixels'] = document['pixels'][1:]
        self.assertTrue(self.request('decode', document)['diagnostics'])


if __name__ == '__main__':
    unittest.main()
