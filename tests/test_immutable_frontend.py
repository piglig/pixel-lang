"""Compiler frontend components execute on VM 0.9 and publish immutable ASTs."""
import json
from pathlib import Path
import unittest

from pixellang.code_image import CodeImage
from pixellang.model import PixelError
from pixellang.vm import VM, Ref
from tests.native_compiler import compile_native_sources

FILES = ('model.pxl', 'lexer.pxl', 'parser.pxl', 'project.pxl', 'syntax_index.pxl', 'bindings.pxl', 'types.pxl')
DRIVER = '''import "types.pxl" as types
import "bindings.pxl" as bindings
import "lexer.pxl" as lexer
import "parser.pxl" as parser
import "project.pxl" as project
import "syntax_index.pxl" as syntaxIndex
record Request { source: string, text: string, operation: string, editor: bool, files: map[string, string] }
effect fn main() {
 match jsonDecode::[Request](input()) {
  .Ok(request) => { Dispatch(request) },
  .Err(error) => { print(error.message) }
 }
}
effect fn Dispatch(request: Request) {
 if request.operation == "types" { print(types.Analyze(bindings.Bind(project.Build(request.files, request.source)))); return }
 if request.operation == "bind" { print(bindings.Bind(project.Build(request.files, request.source))); return }
 if request.operation == "project" { print(project.Build(request.files, request.source)); return }
 if request.operation == "owners" { print(syntaxIndex.Owners(parser.Parse(request.source, request.text))); return }
 if request.operation == "scan" {
  if request.editor { print(lexer.ScanEditor(request.source, request.text)) }
  else { print(lexer.Scan(request.source, request.text)) }
 } else {
  if request.editor { print(parser.ParseEditor(request.source, request.text)) }
  else { print(parser.Parse(request.source, request.text)) }
 }
}'''


class ImmutableFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        files = {name: (root/'selfhost'/name).read_text() for name in FILES}
        cls.image = CodeImage(compile_native_sources({**files, 'main.pxl': DRIVER}))

    def request(self, text='', operation='parse', *, editor=False, files=None):
        vm = VM(self.image, input_text=json.dumps(dict(source='main.pxl', text=text,
            operation=operation, editor=editor, files=files or {})), max_steps=40000000,
            max_depth=1024, max_heap_items=16000000, max_heap_objects=1000000,
            max_output_chars=64000000)
        return vm.run()[0], vm

    def test_ast_nodes_and_children_are_readonly(self):
        parsed, vm = self.request('record Item { value: int }\nfn main() { var x: [int] = [1] }')
        self.assertEqual(parsed['diagnostics'], [])
        node_types = {name for name, fields in vm.bytecode['records'].items()
                      if set(fields) == {'kind','text','children','start','end'}}
        found = []
        for address, obj in vm.heap.items():
            if obj['type'] not in node_types:
                continue
            found.append(address)
            self.assertTrue(obj['immutable'])
            child = obj['items']['children']
            self.assertTrue(vm.heap[child.address]['immutable'])
            with self.assertRaises(PixelError):
                vm.require_mutable(Ref(address), None)
        self.assertTrue(found)
        declaration = next(node for node in parsed['nodes'] if node['kind'] == 'record')
        self.assertEqual(declaration['start'], 0)
        self.assertTrue(any(parsed['nodes'][child]['kind'] == 'field_decl' for child in declaration['children']))

    def test_bound_scope_tables_are_readonly(self):
        bound, vm = self.request(operation='bind', files={
            'main.pxl': 'fn main() { let outer = 1; let f = fn() = outer; f() }'})
        self.assertEqual(bound['diagnostics'], [])
        self.assertEqual(len(bound['captures']), 1)
        scope_types = {name for name, fields in vm.bytecode['records'].items()
                       if set(fields) == {'parent', 'owner', 'module', 'node', 'symbols'}}
        found = []
        for address, obj in vm.heap.items():
            if obj['type'] not in scope_types:
                continue
            found.append(address)
            self.assertTrue(obj['immutable'])
            names = obj['items']['symbols']
            self.assertTrue(vm.heap[names.address]['immutable'])
            with self.assertRaises(PixelError):
                vm.require_mutable(names, None)
        self.assertTrue(found)

    def test_type_arena_publishes_immutable_entries(self):
        resolved, vm = self.request(operation='types', files={
            'main.pxl': 'record Box[T] { value: T }\nfn use(x: Box[[int]]) {}'})
        self.assertEqual(resolved['diagnostics'], [])
        type_info = {name for name, fields in vm.bytecode['records'].items()
                     if set(fields) == {'kind', 'name', 'module', 'declaration', 'arguments', 'fields', 'variants'}}
        found = []
        for address, obj in vm.heap.items():
            if obj['type'] not in type_info:
                continue
            found.append(address)
            self.assertTrue(obj['immutable'])
            for field in ('arguments', 'fields', 'variants'):
                child = obj['items'][field]
                self.assertTrue(vm.heap[child.address]['immutable'])
            with self.assertRaises(PixelError):
                vm.require_mutable(Ref(address), None)
        self.assertTrue(found)
        symbol = next(s for s in resolved['bound']['symbols'] if s['name'] == 'use')
        self.assertEqual(resolved['types'][symbol['typeId']]['kind'], 'function')

    def test_late_type_diagnostics_are_preserved(self):
        resolved, _ = self.request(operation='types', files={
            'main.pxl': 'record Bad { values: map[[int], int] }\nfn main() {}'})
        self.assertEqual([d['message'] for d in resolved['diagnostics']],
                         ['Map key type requires Hashable'])
        self.assertEqual(resolved['diagnostics'][0]['phase'], 'type')

    def test_editor_recovery_keeps_following_declaration(self):
        parsed, _ = self.request('let broken = )\nlet good = 2\n', editor=True)
        self.assertTrue(parsed['diagnostics'])
        self.assertTrue(any(node['kind']=='let' and node['text']=='good' for node in parsed['nodes']))

    def test_nearest_function_owner_is_preserved(self):
        source = 'fn main() { let outside = 1; fn inner() { let inside = 2 }; let f = fn() = outside }'
        parsed, _ = self.request(source)
        owners, _ = self.request(source, 'owners')
        nodes = parsed['nodes']
        main = next(i for i, node in enumerate(nodes) if node['kind']=='fn' and node['text']=='main')
        inner = next(i for i, node in enumerate(nodes) if node['kind']=='fn' and node['text']=='inner')
        inside = next(i for i, node in enumerate(nodes) if node['kind']=='let' and node['text']=='inside')
        outside = next(i for i, node in enumerate(nodes) if node['kind']=='let' and node['text']=='outside')
        self.assertEqual((owners[inner], owners[inside], owners[outside]), (main, inner, main))


if __name__ == '__main__':
    unittest.main()
