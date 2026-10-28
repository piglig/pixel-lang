"""Editor projections obey the compiler's immutable publication boundaries."""
import json
from pathlib import Path
import re
import unittest

from pixellang.vm import VM
from pixellang.native_editor import NativeSymbolIndex
from tests.native_compiler import compile_native_sources

DRIVER = '''import "model.pxl" as model
import "source_format.pxl" as formatter
import "source_info.pxl" as info
import "source_writer.pxl" as writer
import "editor_index.pxl" as editor
import "flow_projection.pxl" as graphs
import "parser.pxl" as parser
import "project.pxl" as project
import "bindings.pxl" as bindings
import "types.pxl" as types
import "checker.pxl" as checker
record Request { operation: string, files: map[string, string] }
effect fn main() {
 match jsonDecode::[Request](input()) { .Ok(request)=>{Dispatch(request)}, .Err(error)=>{print(error.message)} }
}
effect fn Dispatch(request:Request) {
 if request.operation == "format" { print(jsonFrom(formatter.Format("main.pxl", request.files["main.pxl"]))); return }
 if request.operation == "info" { print(jsonFrom(info.Describe(parser.Parse("main.pxl", request.files["main.pxl"]), "main.pxl"))); return }
 let sources = project.BuildEditorParsed(request.files, "main.pxl", map[string, model.Parsed]{}, ["main.pxl"])
 if request.operation == "editor" { print(jsonFrom(editor.Analyze(sources))); return }
 if request.operation == "rewrite" { print(jsonFrom(writer.Build(sources, map[string, string]{}, map[string, string]{}))); return }
 print(jsonFrom(graphs.Build(checker.Check(types.Analyze(bindings.Bind(sources))))))
}
'''


def sources():
    root = Path(__file__).resolve().parents[1] / 'selfhost'
    files = {'main.pxl': DRIVER}
    def add(name):
        if name in files:
            return
        files[name] = (root / name).read_text()
        for child in re.findall(r'^import "([^"]+)"', files[name], re.M):
            add(child)
    for name in re.findall(r'^import "([^"]+)"', DRIVER, re.M):
        add(name)
    return files


class ImmutableEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bytecode = compile_native_sources(sources())

    def query(self, operation, source):
        files = source if isinstance(source, dict) else {'main.pxl': source}
        vm = VM(self.bytecode, input_text=json.dumps(dict(operation=operation, files=files)),
                max_steps=40_000_000, max_depth=1024, max_heap_items=16_000_000,
                max_heap_objects=1_000_000, max_output_chars=64_000_000)
        return json.loads(vm.run()[0])

    def test_format_is_idempotent_and_retains_var_and_comments(self):
        source = '// builder\neffect fn main(){\nvar values:[int]=[1,2]\nvalues.append(3)\nprint(values)\n}\n'
        result = self.query('format', source)
        self.assertEqual(result['diagnostics'], [])
        self.assertIn('// builder', result['text'])
        self.assertIn('var values', result['text'])
        self.assertEqual(self.query('format', result['text']), result)

    def test_rewrite_preserves_value_copy_and_explicit_writeback(self):
        source = 'record Box { values: [int] }\neffect fn main() { let b = Box{values: [1]}; var copy = b; copy.values.append(2); print(copy.values) }'
        result = self.query('rewrite', source)
        self.assertEqual(result['diagnostics'], [])
        rewritten = result['files']['main.pxl']
        self.assertIn('values: [int]', rewritten)
        self.assertIn('var copy', rewritten)
        self.assertEqual(self.query('editor', rewritten)['diagnostics'], [])

    def test_source_info_retains_interfaces_and_test_discovery(self):
        source = 'import "lib.pxl" as lib\nexport fn value(n: int) -> int = n + 1\nfn test_valid() {}\nfn test_invalid(x: int) {}\neffect fn main() {}'
        result = self.query('info', source)
        self.assertEqual(result['imports'], ['lib.pxl'])
        self.assertTrue(result['hasMain'])
        self.assertEqual(result['tests'], [{'name': 'test_valid', 'valid': True}, {'name': 'test_invalid', 'valid': False}])
        self.assertEqual(self.query('info', source.replace('n + 1', 'n + 2'))['interface'], result['interface'])
        self.assertNotEqual(self.query('info', source.replace('n: int', 'n: string'))['interface'], result['interface'])

    def test_editor_materializes_generic_fields_without_mutating_entries(self):
        result = self.query('editor', 'record Box[T] { value: T }\neffect fn main() { let box = Box::[int]{value: 1}; print(box.value) }')
        self.assertEqual(result['diagnostics'], [])
        arena = result['resolved']['types']
        boxes = [t for t in arena if t['name'] == 'Box' and t['fields'] and arena[t['fields'][0]['typeId']]['name'] == 'int']
        self.assertTrue(boxes)

    def test_editor_type_labels_retain_permissions_and_both_map_arguments(self):
        source = 'fn Read[T](x: T) -> T = x\neffect fn main() { var values: map[string, int] = map[string, int]{"a": 1}; print(values) }'
        result = self.query('editor', source)
        self.assertEqual(result['diagnostics'], [])
        index = NativeSymbolIndex({'main.pxl': source}, 'main.pxl', result)
        labels = {index.display_type(i) for i in range(len(index.types))}
        self.assertIn('map[string, int]', labels)
        self.assertIn('T', labels)

    def test_editor_keeps_valid_symbols_after_parse_error(self):
        result = self.query('editor', 'fn good() -> int = 7\neffect fn main() { let broken = }')
        self.assertTrue(result['diagnostics'])
        self.assertIn('good', [symbol['name'] for symbol in result['resolved']['bound']['symbols']])

    def completions(self, text):
        offset = text.index('|')
        text = text.replace('|', '', 1)
        def analyze(files):
            return NativeSymbolIndex(files, 'main.pxl', self.query('editor', files))
        return analyze({'main.pxl': text}).complete('main.pxl', offset, analyze=analyze)

    def test_member_completion_projects_receiver_permissions(self):
        declaration = 'record Box { values: [int] }\n'
        for receiver, expected in [('Box', '[int]'), ('inout Box', '[int]')]:
            with self.subTest(receiver=receiver):
                result = self.completions(declaration + 'fn inspect(box: ' + receiver + ') { box.val| }\neffect fn main() {}')
                self.assertEqual(result, [dict(label='values', kind='Field', detail=expected)])

    def test_generic_member_projection_preserves_function_contracts(self):
        source = 'record Box[T] { value: T, callback: fn(inout [int]) -> [int] }\nfn inspect(box: Box[[int]]) { box.| }\neffect fn main() {}'
        fields = {item['label']: item['detail'] for item in self.completions(source)}
        self.assertEqual(fields['value'], '[int]')
        self.assertEqual(fields['callback'], 'fn(inout [int]) -> [int]')

    def test_mutable_member_navigation_keeps_nominal_identity(self):
        source = 'record Box { values: [int] }\neffect fn inspect(box: inout Box) { print(box.values) }\neffect fn main() {}'
        result = self.query('editor', source)
        self.assertEqual(result['diagnostics'], [])
        index = NativeSymbolIndex({'main.pxl': source}, 'main.pxl', result)
        offset = source.index('box.values') + len('box.')
        self.assertEqual(index.hover('main.pxl', offset)['label'], 'values: [int]')
        self.assertEqual(index.definition('main.pxl', offset)['start']['offset'], source.index('values:'))

    def test_enum_member_views_do_not_change_declaration_schemas(self):
        source = 'enum Choice { Value(items: [int]), Empty }\nfn inspect(a: Choice, b: inout Choice) {}\neffect fn main() {}'
        result = self.query('editor', source)
        self.assertEqual(result['diagnostics'], [])
        index = NativeSymbolIndex({'main.pxl': source}, 'main.pxl', result)
        variants = []
        for identity, typ in enumerate(index.types):
            view = index.member_info(identity)
            if view and view['kind'] == 'enum' and view['name'] == 'Choice':
                payload = view['variants'][0]['fields'][0]['typeId']
                variants.append((typ['kind'], index.display_type(payload)))
        self.assertIn(('enum', '[int]'), variants)
        self.assertTrue(all(payload == '[int]' for _, payload in variants))
        declared = next(t for t in index.types if t['kind'] == 'enum' and t['name'] == 'Choice')
        self.assertEqual(index.display_type(declared['variants'][0]['fields'][0]['typeId']), '[int]')

    def test_flow_projection_retains_states_and_typed_endpoints(self):
        source = 'fn Identity(value: int) -> int = value\nflow Counter() step(delta: int) -> int { state total: int = 0; let pass = Identity(delta); next total = pass; output total }\neffect fn main() {}'
        result = self.query('graphs', source)
        self.assertEqual(result['diagnostics'], [])
        graph = result['graphs'][0]
        self.assertEqual(graph['states'][0]['name'], 'total')
        self.assertEqual(graph['values'][0]['type'], 'int')
        self.assertEqual(graph['inputs'][0]['name'], 'delta')
        self.assertEqual(graph['updates'][0]['state'], 'total')
        self.assertEqual(graph['outputs'][0]['expression']['type'], 'int')
