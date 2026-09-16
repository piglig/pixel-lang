import json
import unittest
from pathlib import Path

from pixellang.codec import encode_token, new_source
from pixellang.project import compile_project
from pixellang.vm import VM


class PixelExpressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        files = {p.name: p.read_text() for p in (root / 'selfhost').glob('*.pxl')}
        files['main.pxl'] = '''import "pixel_decode.pxl" as decoder
import "pixel_reader.pxl" as reader
import "pixel_expressions.pxl" as expressions
import "spatial_layout.pxl" as layout
import "model.pxl" as model
record Output { root: int, nodes: [model.Node], diagnostics: [decoder.Diagnostic] }
fn main() {
 let source = jsonDecode[layout.Source](input())
 let decoded = decoder.Decode(source, "fixture.pixel")
 let r = reader.New("fixture.pixel", 0, decoded.tokens)
 let root = expressions.Parse(r)
 if r.index != len(r.tokens) { reader.Problem(r, "Unexpected trailing token") }
 print(jsonFrom(Output{root, nodes: r.nodes, diagnostics: r.diagnostics}))
}'''
        cls.files = files
        cls.compiler = compile_project(files)[1].bytecode

    def source(self, tokens):
        colors = [color for kind, value in tokens for color in encode_token(kind, value)]
        return new_source(max(1, len(colors)), 1, [dict(position=[i, 0], rgba=c) for i, c in enumerate(colors)])

    def parse(self, tokens):
        return json.loads(VM(self.compiler, input_text=json.dumps(self.source(tokens)), max_steps=1_000_000).run()[0])

    def tree(self, result, index=None):
        node = result['nodes'][result['root'] if index is None else index]
        return (node['kind'], node['text'], [self.tree(result, child) for child in node['children']])

    def test_precedence_and_associativity(self):
        result = self.parse([('int', 9), ('op', '-'), ('int', 2), ('op', '*'), ('int', 3), ('op', '-'), ('int', 1)])
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(self.tree(result), ('binary', '-', [('binary', '-', [
            ('literal_int', '9', []), ('binary', '*', [('literal_int', '2', []), ('literal_int', '3', [])])]), ('literal_int', '1', [])]))
        result = self.parse([('op', 'not'), ('bool', False), ('op', 'and'), ('bool', True)])
        self.assertEqual(self.tree(result), ('binary', '&&', [('unary', '!', [('literal_keyword', 'false', [])]), ('literal_keyword', 'true', [])]))

    def test_qualified_generic_call_and_field_index(self):
        result = self.parse([('id', 1), ('punct', '.'), ('id', 7), ('punct', '['), ('type', 'int'), ('punct', ']'),
                            ('punct', '('), ('int', 4), ('punct', ')'), ('punct', '@'), ('str', 'values'), ('punct', '['), ('int', 0), ('punct', ']')])
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(self.tree(result), ('index', '', [('field', 'values', [('call', '', [
            ('specialize', '', [('field', '_pixel_7', [('name', '_module_1', [])]), ('type', 'int', [])]),
            ('literal_int', '4', [])])]), ('literal_int', '0', [])]))

    def test_literals_and_malformed_expressions(self):
        for kind, value, text in [('int', -2**63, str(-2**63)), ('str', '界😀', '界😀'), ('float64', -0.0, '-0.0')]:
            result = self.parse([(kind, value)])
            self.assertEqual(result['diagnostics'], [])
            expected = ('literal_' + kind, text, [])
            if kind == 'int' and value < 0:
                expected = ('unary', '-', [('literal_int', text[1:], [])])
            self.assertEqual(self.tree(result), expected)
        for tokens in ([], [('int', 1), ('op', '+')], [('id', 1), ('punct', '[')], [('punct', '('), ('int', 1)], [('int', 1), ('int', 2)]):
            self.assertTrue(self.parse(tokens)['diagnostics'])

    def test_typed_arrays_and_maps(self):
        array = [('type', 'int'), ('punct', '['), ('punct', ']'), ('punct', '('),
                 ('punct', '['), ('int', 1), ('punct', ','), ('int', 2), ('punct', ']'), ('punct', ')')]
        result = self.parse(array)
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(self.tree(result), ('annotate', '', [
            ('array', '', [('literal_int', '1', []), ('literal_int', '2', [])]),
            ('type_array', '', [('type', 'int', [])])]))
        mapping = [('type', 'map'), ('punct', '('), ('type', 'int'), ('punct', ')'),
                   ('punct', '('), ('punct', '{'), ('str', '界'), ('punct', ':'), ('int', 3), ('punct', '}'), ('punct', ')')]
        result = self.parse(mapping)
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(self.tree(result), ('map', '', [('type', 'int', []),
            ('map_entry', '', [('literal_str', '界', []), ('literal_int', '3', [])])]))
        self.assertEqual(self.tree(self.parse([('punct', '['), ('punct', ']')])), ('array', '', []))

    def test_generic_record_constructor(self):
        tokens = [('type', 'record'), ('punct', '('), ('id', 1), ('punct', '.'), ('id', 7),
                  ('punct', '['), ('type', 'int'), ('punct', ']'), ('punct', ')'),
                  ('punct', '('), ('punct', '{'), ('str', 'value'), ('punct', ':'), ('int', 5), ('punct', '}'), ('punct', ')')]
        result = self.parse(tokens)
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(self.tree(result), ('construct', '', [
            ('specialize', '', [('field', '_pixel_7', [('name', '_module_1', [])]), ('type', 'int', [])]),
            ('initializer', 'value', [('literal_int', '5', [])])]))
        bad = list(tokens)
        bad[11] = ('int', 0)
        self.assertTrue(self.parse(bad)['diagnostics'])

    def test_option_variant_and_match_bindings(self):
        option = [('type', 'Option'), ('punct', '('), ('type', 'int'), ('punct', ')')]
        variant = [('keyword', 'variant'), ('punct', '(')] + option + [
            ('punct', ','), ('str', 'Some'), ('punct', ','), ('int', 8), ('punct', ')')]
        result = self.parse(variant)
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(self.tree(result), ('annotate', '', [
            ('call', '', [('name', 'Some', []), ('literal_int', '8', [])]),
            ('type', 'Option', [('type', 'int', [])])]))
        tokens = [('keyword', 'match'), ('punct', '(')] + variant + [('punct', ')'),
            ('punct', '{'), ('keyword', 'case')] + option + [('str', 'Some'),
            ('punct', '('), ('id', 9), ('punct', ')'), ('punct', '=>'), ('id', 9),
            ('punct', ','), ('keyword', 'case'), ('keyword', 'otherwise'), ('punct', '=>'), ('int', 0), ('punct', '}')]
        result = self.parse(tokens)
        self.assertEqual(result['diagnostics'], [])
        tree = self.tree(result)
        self.assertEqual(tree[0], 'match')
        self.assertEqual(tree[2][1], ('arm', '', [
            ('variant_pattern', 'Some', [('type', 'Option', [('type', 'int', [])])]),
            ('binding', '_pixel_9', []), ('name', '_pixel_9', [])]))
        self.assertEqual(tree[2][2], ('arm', '', [('type', '_', []), ('literal_int', '0', [])]))
        for name, payload in [('Some', []), ('None', [('punct', ','), ('int', 1)]), ('Invalid', [])]:
            bad = [('keyword', 'variant'), ('punct', '(')] + option + [('punct', ','), ('str', name)] + payload + [('punct', ')')]
            self.assertTrue(self.parse(bad)['diagnostics'])

    def test_expression_reader_compiles_with_pixellang(self):
        from pixellang.bootstrap import compile_with
        root = Path(__file__).resolve().parents[1]
        seed = compile_project({p.name: p.read_text() for p in (root / 'selfhost').glob('*.pxl')})[1].bytecode
        generated, _ = compile_with(seed, self.files)
        tokens = [('builtin', 'len'), ('punct', '('), ('str', '界😀'), ('punct', ')'), ('op', '+'), ('int', 3)]
        actual = json.loads(VM(generated, input_text=json.dumps(self.source(tokens)), max_steps=1_000_000).run()[0])
        self.assertEqual(actual, self.parse(tokens))

    def test_parsed_collections_execute_through_pixellang_backend(self):
        files = dict(self.files)
        files['main.pxl'] = '''import "pixel_decode.pxl" as decoder
import "pixel_reader.pxl" as reader
import "pixel_expressions.pxl" as expressions
import "spatial_layout.pxl" as layout
import "model.pxl" as model
import "bindings.pxl" as bindings
import "types.pxl" as types
import "checker.pxl" as checker
import "instances.pxl" as instances
import "closures.pxl" as closures
import "ir.pxl" as ir
import "assembler.pxl" as assembler
fn main() {
 let source = jsonDecode[layout.Source](input())
 let decoded = decoder.Decode(source, "fixture.pixel")
 let r = reader.New("fixture.pixel", 0, decoded.tokens)
 let expression = expressions.Parse(r)
 assert(len(r.diagnostics) == 0)
 assert(r.index == len(r.tokens))
 let callee = reader.Add(r, "name", "print", [], 0)
 let call = reader.Add(r, "call", "", [callee, expression], 0)
 let statement = reader.Add(r, "expression_stmt", "", [call], 0)
 let body = reader.Add(r, "block", "", [statement], 0)
 let main = reader.Add(r, "fn", "main", [body], 0)
 let tokens: [model.Token] = []
 for index, token in r.tokens {
   append(tokens, model.Token{kind: "pixel", text: "", value: "", start: index, end: index + 1, line: token.position[1] + 1, column: token.position[0] + 1})
 }
 let syntax = model.Parsed{tokens, nodes: r.nodes, roots: [main], diagnostics: []}
 let module = model.Module{path: "fixture.pixel", syntax, imports: [], declarations: [model.Declaration{name: "main", kind: "fn", node: main, exported: false}]}
 let project = model.Project{modules: [module], diagnostics: []}
 let checked = checker.Check(types.Analyze(bindings.Bind(project)))
 let code = assembler.Assemble(ir.Build(closures.Build(instances.Build(checked))))
 print(jsonFrom(code))
}'''
        driver = compile_project(files)[1].bytecode
        cases = [
            ([('type', 'int'), ('punct', '['), ('punct', ']'), ('punct', '('), ('punct', '['),
              ('int', -2**63), ('punct', ']'), ('punct', ')'), ('punct', '['), ('int', 0), ('punct', ']')], [-2**63]),
            ([('type', 'map'), ('punct', '('), ('type', 'int'), ('punct', ')'), ('punct', '('),
              ('punct', '{'), ('str', 'a'), ('punct', ':'), ('int', 3), ('punct', '}'), ('punct', ')'),
              ('punct', '['), ('str', 'a'), ('punct', ']')], [3]),
        ]
        option = [('type', 'Option'), ('punct', '('), ('type', 'int'), ('punct', ')')]
        cases.append(([('keyword', 'match'), ('punct', '('), ('keyword', 'variant'), ('punct', '(')] + option + [
            ('punct', ','), ('str', 'Some'), ('punct', ','), ('int', 8), ('punct', ')'), ('punct', ')'),
            ('punct', '{'), ('keyword', 'case')] + option + [('str', 'Some'), ('punct', '('), ('id', 9),
            ('punct', ')'), ('punct', '=>'), ('id', 9), ('punct', ','), ('keyword', 'case'),
            ('keyword', 'otherwise'), ('punct', '=>'), ('int', 0), ('punct', '}')], [8]))
        from unittest.mock import patch
        from tests.selfhost_runtime import SELFHOST_HEAP_LIMITS
        for tokens, expected in cases:
            with patch('pixellang.text.parse_text', side_effect=AssertionError('host parse')), patch(
                'pixellang.backend.lower', side_effect=AssertionError('host lower')), patch(
                'pixellang.semantics.Checker.check', side_effect=AssertionError('host check')), patch(
                'pixellang.backend.assemble', side_effect=AssertionError('host assemble')):
                result = json.loads(VM(driver, **SELFHOST_HEAP_LIMITS, input_text=json.dumps(self.source(tokens)),
                                       max_steps=100_000_000, max_depth=1024).run()[0])
            self.assertEqual(result['diagnostics'], [])
            self.assertEqual(VM(result['bytecode']).run(), expected)
