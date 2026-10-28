import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.native_compiler import compile_native_sources
from pixellang.bootstrap import compile_with
from pixellang.codec import new_source, rgba
from pixellang.compiler_client import CompilerClient
from pixellang.vm import VM
from pixellang.model import PixelError
from tests.selfhost_runtime import SELFHOST_HEAP_LIMITS


class PixelStatementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = CompilerClient()
        cls.addClassCleanup(cls.client.close)
        root = Path(__file__).resolve().parents[1]
        files = {p.name: p.read_text() for p in (root / 'selfhost').glob('*.pxl')}
        files['main.pxl'] = '''import "pixel_decode.pxl" as decoder
import "pixel_regions.pxl" as regions
import "pixel_statements.pxl" as statements
import "spatial_layout.pxl" as layout
import "model.pxl" as model
import "bindings.pxl" as bindings
import "types.pxl" as types
import "checker.pxl" as checker
import "instances.pxl" as instances
import "closures.pxl" as closures
import "ir.pxl" as ir
import "assembler.pxl" as assembler
record Request { document: layout.Source, execute: bool }
effect fn main() {
 match jsonDecode::[Request](input()) { .Ok(request) => Dispatch(request), .Err(error) => print(error) }
}
effect fn Dispatch(request: Request) {
 let parsed = statements.Parse(regions.Analyze(decoder.Decode(request.document, "fixture.pixel"), request.document.spatial.links, "fixture.pixel"), "fixture.pixel", 0)
 if !request.execute || len(parsed.diagnostics) > 0 { print(jsonFrom(parsed)); return }
 assert(len(parsed.entries) == 1)
 let entry = parsed.syntax.nodes[parsed.entries[0]]
 var declarations: [model.Declaration] = []
 for root in parsed.syntax.roots {
   var index = root
   var exported = false
   if parsed.syntax.nodes[index].kind == "export" { index = parsed.syntax.nodes[index].children[0]; exported = true }
   let node = parsed.syntax.nodes[index]
   declarations.append(model.Declaration{name: node.text, kind: node.kind, node: index, exported})
 }
 let module = model.Module{path: "fixture.pixel", syntax: parsed.syntax, imports: [], declarations: declarations}
 let project = model.Project{entryName: entry.text, modules: [module], diagnostics: []}
 let checked = checker.Check(types.Analyze(bindings.Bind(project)))
 var planned = closures.Build(instances.Build(checked))
 print(jsonFrom(assembler.Assemble(ir.Build(&planned))))
}'''
        cls.files = files
        cls.compiler = compile_native_sources(files)

    def parse(self, document, execute=True, compiler=None):
        document = dict(document)
        if 'bundle' in document:
            self.assertEqual(document.pop('bundle')['modules'], {})
        with patch('pixellang.text.parse_text', side_effect=AssertionError('host parse')):
            return json.loads(VM(compiler or self.compiler, **SELFHOST_HEAP_LIMITS,
                input_text=json.dumps(dict(document=document, execute=execute)),
                max_steps=100_000_000, max_depth=1024).run()[0])

    def pixels(self, source):
        return self.client.request('pixels', files={'main.pxl': source})['result']['document']

    def assert_program(self, source, expected):
        document = self.pixels(source)
        result = self.parse(document)
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(VM(result['bytecode'], max_steps=1_000_000).run(), expected)

    def test_recursive_functions_and_branching(self):
        self.assert_program('fn fact(n: int) -> int { if n <= 1 { return 1 } else { return n * fact(n - 1) } }\neffect fn main() { print(fact(6)) }', [720])

    def test_collections_loops_assignments_and_results(self):
        self.assert_program('effect fn main() { var sum = 0; for index, value in [1, 2, 3] { if index == 1 { continue }; sum += value }; var i = 0; while i < 4 { i += 1; if i == 3 { break } }; print(sum); print(i); match parseInt("bad") { .Ok(value) => print(value), .Err(error) => print(error.code) } }', [4, 3, 'numeric.invalid_text'])

    def test_generic_records_enums_and_statement_matches(self):
        self.assert_program('record Box[T] { value: T }\nenum Choice[T] { Value(item: T), Empty }\nfn identity[T](value: T) -> T = value\neffect fn main() { let box = Box::[int]{value: identity(7)}; print(box.value); let choice = Choice::[int].Value(8); match choice { Choice[int].Value(number) => { print(number) }, Choice[int].Empty => { print(0) } } }', [7, 8])

    def test_orphan_and_incomplete_statements_are_rejected(self):
        for name in ('else', 'if', 'while', 'match'):
            source = new_source(1, 1, [dict(position=[0, 0], rgba=rgba('keyword', name))])
            result = self.parse(source, False)
            self.assertTrue(result['diagnostics'])
            self.assertEqual(result['syntax']['roots'], [])

    def test_retired_exception_keywords_have_no_semantic_encoding(self):
        for name in ('try', 'catch'):
            with self.subTest(name=name), self.assertRaises(PixelError):
                rgba('keyword', name)

    def test_invalid_entry_and_trailing_header_tokens(self):
        source = self.pixels('effect fn main() { print(1) }')
        for pixel in source['pixels']:
            if pixel['position'][1] == 0 and pixel['rgba'] == rgba('type', 'unit'):
                pixel['rgba'] = rgba('type', 'int')
        result = self.parse(source, False)
        self.assertIn('Entry', result['diagnostics'][0]['message'])
        self.assertEqual(result['entries'], [])
        source = self.pixels('effect fn main() { print(1) }')
        x = max(p['position'][0] for p in source['pixels'] if p['position'][1] == 0) + 1
        source['pixels'].append(dict(position=[x, 0], rgba=rgba('id', 99)))
        source['dimensions'][0] = max(source['dimensions'][0], x + 1)
        result = self.parse(source, False)
        self.assertIn('Unexpected token', result['diagnostics'][0]['message'])
        self.assertEqual(result['syntax']['roots'], [])

    def test_statement_parser_compiles_with_pixellang(self):
        root = Path(__file__).resolve().parents[1]
        seed = compile_native_sources({p.name: p.read_text() for p in (root / 'selfhost').glob('*.pxl')})
        # Keep this component bootstrap bounded by using a parser-only driver.
        files = dict(self.files)
        files['main.pxl'] = '''import "pixel_decode.pxl" as decoder
import "pixel_regions.pxl" as regions
import "pixel_statements.pxl" as statements
import "spatial_layout.pxl" as layout
record Request { document: layout.Source, execute: bool }
effect fn main() {
 match jsonDecode::[Request](input()) { .Ok(request) => Dispatch(request), .Err(error) => print(error) }
}
effect fn Dispatch(request: Request) {
 print(jsonFrom(statements.Parse(regions.Analyze(decoder.Decode(request.document, "fixture.pixel"), request.document.spatial.links, "fixture.pixel"), "fixture.pixel", 0)))
}'''
        generated, _ = compile_with(seed, files)
        source = self.pixels('effect fn main() { print(42) }')
        self.assertEqual(self.parse(source, False, generated), self.parse(source, False))
