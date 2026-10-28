"""Single fn syntax preserves explicit effects through both text renderers."""
import json
from pathlib import Path
import unittest
from pixellang.vm import VM
from tests.native_compiler import compile_native_sources

ROOT = Path(__file__).resolve().parents[1]

class EffectParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files = {name: (ROOT / 'selfhost' / name).read_text() for name in
                 ('model.pxl', 'lexer.pxl', 'parser.pxl', 'source_format.pxl', 'source_writer.pxl', 'failures.pxl')}
        files['main.pxl'] = '''import "model.pxl" as model
import "parser.pxl" as parser
import "source_format.pxl" as format
import "source_writer.pxl" as writer
record Request { source:string, names:map[string,string] }
effect fn main() {
 match jsonDecode::[Request](input()) { .Ok(request)=>{ Run(request) }, .Err(error)=>{ print(error) } }
}
effect fn Run(request: Request) {
 let source = request.source
 let parsed = parser.Parse("main.pxl", source)
 print(jsonFrom(parsed))
 print(jsonFrom(format.Format("main.pxl", source)))
 let module = model.Module{path: "main.pxl", syntax: parsed, imports: [], declarations: []}
 let project = model.Project{modules: [module], diagnostics: parsed.diagnostics, entryName: "main"}
 let recovered = writer.Build(project, request.names, map[string,string]{})
 if len(recovered.diagnostics) == 0 {
  let original = recovered.files["main.pxl"]
  var edited = recovered
  edited.files["main.pxl"] = "changed"
  assert(recovered.files["main.pxl"] == original)
 }
 print(jsonFrom(recovered))
}
'''
        cls.code = compile_native_sources(files)

    def parse(self, source, names=None):
        request = json.dumps(dict(source=source, names=names or {}))
        output = VM(self.code, input_text=request, max_steps=4000000).run()
        return tuple(json.loads(item) for item in output)

    def test_effects_survive_format_and_ast_source_recovery(self):
        source = '''export effect fn apply[T](value: T, callback: effect fn(T)) { callback(value) }
fn plain(value: int) -> int = value
 effect fn main() {
 let callback: effect fn(int) = effect fn(value: int) { print(value) }
 apply::[int](plain(1), callback)
}
'''
        parsed, formatted, recovered = self.parse(source)
        self.assertEqual(parsed['diagnostics'], [])
        self.assertEqual(sum(n['kind'] == 'effect_marker' for n in parsed['nodes']), 2)
        self.assertEqual([n['text'] for n in parsed['nodes'] if n['kind'] == 'lambda'], ['effect'])
        self.assertEqual([n['text'] for n in parsed['nodes'] if n['kind'] == 'type_fn'], ['effect', 'effect'])
        for text in (formatted['text'], recovered['files']['main.pxl']):
            reparsed, again, _ = self.parse(text)
            self.assertEqual(reparsed['diagnostics'], [])
            self.assertEqual([(n['kind'],n['text'],n['children']) for n in reparsed['nodes']],
                             [(n['kind'],n['text'],n['children']) for n in parsed['nodes']])
            _, stable, _ = self.parse(again['text'])
            self.assertEqual(stable['text'], again['text'])

    def test_effect_is_contextual_identifier(self):
        parsed, _, recovered = self.parse('fn effect(value: int) -> int = value\nfn main() { let effect = effect(1) }')
        self.assertEqual(parsed['diagnostics'], [])
        self.assertFalse(any(n['kind'] == 'effect_marker' for n in parsed['nodes']))
        self.assertIn('fn effect(', recovered['files']['main.pxl'])

    def test_component_and_malformed_effect_syntax_rejected(self):
        for source in ('component Old() {}', 'export component Old() {}',
                       'fn f(value: component(int)) {}',
                       'fn main() { let f = component(x: int) = x }',
                       'effect record Wrong {}', 'effect fn main() -> effect int {}'):
            with self.subTest(source=source):
                parsed, _, _ = self.parse(source)
                self.assertTrue(parsed['diagnostics'])

    def test_recovered_expression_flows_keep_positional_calls_and_scoped_names(self):
        source = """import "remote.pxl" as remote
fn Local(p:int) -> int = p
flow Imported() step(value:int) -> int {
 output remote.Step(value)
}
flow LocalFlow() step(value:int) -> int {
 output Local(value)
}
fn main() {}
"""
        parsed, _, recovered = self.parse(source, {'remote:p': 'remoteInput', 'main:p': 'localInput'})
        self.assertEqual(parsed['diagnostics'], [])
        self.assertEqual(recovered['diagnostics'], [])
        text = recovered['files']['main.pxl']
        imported, local = text.split('flow LocalFlow', 1)
        self.assertIn('remote.Step(value)', imported)
        self.assertIn('fn Local(localInput: int)', imported)
        self.assertIn('Local(value)', local)
        self.assertNotIn('remoteInput', imported)
        self.assertNotIn('remoteInput', local)
        reparsed, _, _ = self.parse(text)
        self.assertEqual(reparsed['diagnostics'], [])

    def test_recovered_record_updates_inout_and_value_captures_round_trip(self):
        source = """record Box { values:[int] }
fn Change(value:inout Box) { value.values.append(3) }
effect fn main() {
 let original=Box{values:[1,2]}
 var edited=original with {values:[4]}
 Change(&edited)
 let read=fn() = original.values
 print(read())
}
"""
        parsed, _, recovered = self.parse(source)
        self.assertEqual(parsed['diagnostics'], [])
        reparsed, _, _ = self.parse(recovered['files']['main.pxl'])
        self.assertEqual(reparsed['diagnostics'], [])
        self.assertEqual([(n['kind'], n['text'], n['children']) for n in reparsed['nodes']],
                         [(n['kind'], n['text'], n['children']) for n in parsed['nodes']])
