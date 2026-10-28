import json
import os
from pathlib import Path
import unittest
from contextlib import ExitStack
from unittest.mock import patch
from pixellang.bootstrap_image import HOST_STAGES
from pixellang.compiler_service import CompilerService
from pixellang.native_editor import NativeSymbolIndex

from tests.native_compiler import load_native_compiler


class NativeEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler_path = os.environ.get('PIXELLANG_EDITOR_TEST_COMPILER')
        bytecode = (json.loads(Path(compiler_path).read_text()) if compiler_path
                    else load_native_compiler(driver=None))
        cls.compiler = CompilerService(bytecode)
        cls.addClassCleanup(cls.compiler.close)

    def format(self, text):
        response = self.compiler.request(dict(version=1, operation='format', files={'main.pxl': text}))
        self.assertEqual(response['status'], 'ok', response)
        return response['result']['text']

    def index(self, source, **modules):
        files = {'main.pxl': source, **modules}
        with ExitStack() as guards:
            for target in HOST_STAGES:
                guards.enter_context(patch(target, side_effect=AssertionError('Host frontend used')))
            result = self.compiler.request(dict(version=1, id=1, operation='editor-index', entry='main.pxl', files=files))
        self.assertEqual(result['status'], 'ok', result)
        return NativeSymbolIndex(files, 'main.pxl', result['result']['index'])

    def test_native_frame_function_outline_and_reference_identity(self):
        text = '''frame World = meters
record Cost { weight: int }
fn Weight(edge: Link[World, string, Cost]) -> int = edge.payload.weight
effect fn main() {}
'''
        index = self.index(text)
        self.assertEqual(index.diagnostics, [])
        self.assertEqual([item['name'] for item in index.document_symbols('main.pxl')], ['World', 'Cost', 'Weight', 'main'])
        self.assertEqual(index.hover('main.pxl', text.index('World, string'))['label'], 'frame World')
        self.assertEqual(len(index.references('main.pxl', text.index('World'))), 2)
        field = index.definition('main.pxl', text.index('.weight') + 1)
        self.assertEqual(field['start']['offset'], text.index('weight: int'))
        self.assertEqual(index.hover('main.pxl', text.index('edge.payload'))['label'], 'edge: Link[World, string, Cost]')

    def test_inferred_value_and_generic_callable_hover(self):
        text = 'fn identity[T](value: T) -> T = value\neffect fn main() { let value = identity(42); print(value) }'
        index = self.index(text)
        self.assertEqual(index.diagnostics, [])
        self.assertEqual(index.hover('main.pxl', text.rindex('value'))['label'], 'value: int')
        self.assertEqual(index.hover('main.pxl', text.index('identity(42)'))['label'], 'fn identity[T](value: T) -> T')

    def test_function_callback_hover_and_format_preserve_contract(self):
        text = '''effect fn main() {
let factor = 2
let callback = fn(value: int) -> int = value * factor
print(callback(3))
}'''
        index = self.index(text)
        self.assertEqual(index.diagnostics, [])
        self.assertEqual(index.hover('main.pxl', text.index('callback(3)'))['label'],
                         'callback: fn(int) -> int')
        formatted = self.format(text)
        rebuilt = self.index(formatted)
        self.assertEqual(rebuilt.diagnostics, [])
        self.assertEqual(self.format(formatted), formatted)
        self.assertIn('fn(value: int)', formatted)

    def test_effect_and_inout_contracts_are_visible_in_hover_and_signature(self):
        text = 'effect fn update(items:inout [int]){items.append(1)}\neffect fn main(){var items=[0];let callback=update;callback(&items)}'
        index=self.index(text)
        self.assertEqual(index.diagnostics,[])
        self.assertEqual(index.hover('main.pxl',text.index('update;'))['label'], 'effect fn update(items: inout [int])')
        self.assertEqual(index.hover('main.pxl',text.index('callback(&'))['label'], 'callback: effect fn(inout [int]) -> unit')
        signature=index.signature_help('main.pxl',text.index('callback(&')+len('callback('))
        self.assertEqual(signature['label'],'effect fn callback(arg1: inout [int])')

    def test_unfinished_source_retains_imported_hover_and_outline(self):
        text = 'import "lib.pxl" as lib\neffect fn main() { print(lib.twice(2))\nlet unfinished ='
        index = self.index(text, **{'lib.pxl': 'export fn twice(n: int) -> int = n * 2'})
        self.assertTrue(index.diagnostics)
        self.assertEqual([item['name'] for item in index.document_symbols('main.pxl')], ['main'])
        self.assertEqual(index.hover('main.pxl', text.index('twice'))['label'], 'fn twice(n: int) -> int')

    def test_navigation_recovers_after_malformed_statement_without_accepting_program(self):
        for damaged in ('let unfinished =', 'let unfinished = "open',
                        'let unfinished = unknown()', 'let unfinished: int = true'):
            with self.subTest(damaged=damaged):
                text = 'effect fn main() {\n let amount = 42\n ' + damaged + '\n print(amount)\n'
                index = self.index(text)
                self.assertTrue(index.diagnostics)
                definition = index.definition('main.pxl', text.rindex('amount'))
                self.assertIsNotNone(definition)
                self.assertEqual(definition['start']['offset'], text.index('amount'))
                with ExitStack() as guards:
                    for target in HOST_STAGES:
                        guards.enter_context(patch(target, side_effect=AssertionError('Host frontend used')))
                    result = self.compiler.request(dict(version=1, id=2, operation='compile',
                                                        files={'main.pxl': text}, entry='main.pxl'))
                self.assertEqual(result['status'], 'diagnostics')
                self.assertNotIn('bytecode', result.get('result') or {})

    def test_unclosed_flow_preserves_outline_and_input_binding(self):
        text = 'flow Main() step(value: int) -> int { output value'
        index = self.index(text)
        self.assertTrue(index.diagnostics)
        self.assertEqual([item['name'] for item in index.document_symbols('main.pxl')], ['Main'])
        definition = index.definition('main.pxl', text.rindex('value'))
        self.assertIsNotNone(definition)
        self.assertEqual(definition['start']['offset'], text.index('value'))

    def test_flow_value_state_and_record_field_identity(self):
        text = '''record Packet { value: int }
fn Wrap(value: int) -> Packet = Packet{value}
flow Counter() step(delta: int) -> int {
 state value: int = 0
 let wrap = Wrap(value)
 next value = wrap.value
 output wrap.value
}
effect fn main() {}
'''
        index = self.index(text)
        self.assertEqual(index.diagnostics, [])
        for offset in [text.index('wrap.value'), text.rindex('wrap.value')]:
            self.assertEqual(index.definition('main.pxl', offset)['start']['offset'], text.index('wrap ='))
            self.assertEqual(index.definition('main.pxl', offset + 5)['start']['offset'], text.index('value: int }'))
        state_refs = index.references('main.pxl', text.index('value: int ='))
        self.assertEqual([r['start']['offset'] for r in state_refs],
                         [text.index('value: int ='), text.index('Wrap(value)') + 5, text.index('value = wrap')])
        self.assertEqual(len(index.references('main.pxl', text.index('wrap ='))), 3)
        self.assertEqual(index.definition('main.pxl', text.index('= Wrap') + 2)['start']['offset'], text.index('Wrap(value:'))

    def test_persistent_child_instance_navigation_and_rename(self):
        text = '''flow Child(initial:int) step(delta:int)->int {
 state value=initial
 next value=value+delta
 output value
}
flow Parent() step(delta:int)->int {
 instance child=Child(10)
 let result=child.step(delta)
 output result
}
effect fn main(){}
'''
        index=self.index(text)
        self.assertEqual(index.diagnostics,[])
        self.assertEqual(index.definition('main.pxl',text.index('child.step'))['start']['offset'],text.index('child=Child'))
        self.assertEqual(index.definition('main.pxl',text.index('Child(10)'))['start']['offset'],text.index('Child(initial'))
        changed=self.rename({'main.pxl':text},'main.pxl',text.index('child=Child'),'counter')
        self.assertEqual(changed['main.pxl'],text.replace('child=Child','counter=Child').replace('child.step','counter.step'))

    def test_imported_function_call_and_flow_argument_identity(self):
        text = '''import "lib.pxl" as lib
flow Main() step(input: int) -> int {
 let item = lib.Identity(input)
 output item
}
effect fn main() {}
'''
        library = 'export fn Identity(value: int) -> int = value'
        index = self.index(text, **{'lib.pxl': library})
        self.assertEqual(index.diagnostics, [])
        definition = index.definition('main.pxl', text.index('Identity'))
        self.assertEqual(definition['source'], 'lib.pxl')
        self.assertEqual(definition['start']['offset'], library.index('Identity'))
        definition = index.definition('main.pxl', text.index('Identity(input)') + len('Identity('))
        self.assertEqual(definition['source'], 'main.pxl')
        self.assertEqual(definition['start']['offset'], text.index('input:'))

    def test_native_format_preserves_comment_and_literal_contents(self):
        text = '''frame World = meters
effect fn main() {
let point = Point3::[World]{x: 1.0, y: 2.0, z: 3.0}
/* multi-line { comment
    leave this indentation unchanged
} */
print("literal } { ( )")
}
'''
        index = self.index(text)
        self.assertEqual(index.diagnostics, [])
        formatted = self.format(text)
        self.assertIn('/* multi-line { comment\n    leave this indentation unchanged\n} */', formatted)
        self.assertIn('    print("literal } { ( )")', formatted)
        again = self.index(formatted)
        self.assertEqual(self.format(formatted), formatted)

    def test_malformed_editor_input_retains_diagnostics(self):
        for text in ['effect fn main(', 'record Box { value:', 'frame World =',
                     'effect fn main() { let bad = unknown( }', 'flow Incomplete() step(x: int) -> int { node']:
            with self.subTest(source=text):
                index = self.index(text)
                self.assertTrue(index.diagnostics)
                index.document_symbols('main.pxl')
                compiled = self.compiler.request(dict(version=1, id=2, operation='compile',
                    entry='main.pxl', files={'main.pxl': text}))
                self.assertEqual(compiled['status'], 'diagnostics')
                self.assertIsNone(compiled['result'])

    def rename(self, files, source, offset, name, *, editable_files=None):
        def analyze(snapshot):
            return self.index(snapshot['main.pxl'], **{p: t for p, t in snapshot.items() if p != 'main.pxl'})
        index = analyze(files)
        if editable_files is not None:
            index.editable_files = set(editable_files)
        edits = index.rename(source, offset, name, analyze=analyze)
        changed = dict(files)
        for edit in reversed(edits):
            span = edit['span']
            path = span['source']
            changed[path] = changed[path][:span['start']['offset']] + edit['text'] + changed[path][span['end']['offset']:]
        return changed

    def test_rename_unicode_preserves_comments_and_literals(self):
        text = 'effect fn main() { let 数字 = 1\nprint(数字) // 数字\nprint("数字") }'
        changed = self.rename({'main.pxl': text}, 'main.pxl', text.index('数字'), 'amount')
        self.assertEqual(changed['main.pxl'], 'effect fn main() { let amount = 1\nprint(amount) // 数字\nprint("数字") }')

    def test_rename_rejects_same_typed_capture_in_both_directions(self):
        from pixellang.model import PixelError
        text = 'effect fn main() { let a = 1\nif true { let b = 2\nprint(a)\nprint(b) } }'
        for offset, name in [(text.index('a ='), 'b'), (text.index('b ='), 'a')]:
            with self.subTest(name=name), self.assertRaisesRegex(PixelError, 'binding'):
                self.rename({'main.pxl': text}, 'main.pxl', offset, name)

    def test_rename_implicit_alias_preserves_import_path(self):
        text = 'import "lib.pxl"\neffect fn main() { print(lib.Value()) }'
        changed = self.rename({'main.pxl': text, 'lib.pxl': 'export fn Value() -> int = 42'},
                              'main.pxl', text.index('lib.Value'), 'helper')
        self.assertEqual(changed['main.pxl'], 'import "lib.pxl" as helper\neffect fn main() { print(helper.Value()) }')

    def test_rename_field_and_variable_expand_shorthand_independently(self):
        text = 'record Row { value: int }\neffect fn main() { let value = 1; let row = Row{value}; print(row.value) }'
        for position, name, expected in [(text.index('value:'), 'amount', 'Row{amount: value}'),
                                         (text.index('value ='), 'amount', 'Row{value: amount}')]:
            with self.subTest(position=position):
                changed = self.rename({'main.pxl': text}, 'main.pxl', position, name)
                self.assertIn(expected, changed['main.pxl'])
                self.assertIn('row.amount' if position == text.index('value:') else 'row.value', changed['main.pxl'])

    def test_rename_cross_module_type_and_field(self):
        text = 'import "lib.pxl"\neffect fn main() { let row = lib.Row{value: 1}; print(row.value) }'
        files = {'main.pxl': text, 'lib.pxl': 'export record Row { value: int }'}
        changed = self.rename(files, 'lib.pxl', files['lib.pxl'].index('Row'), 'Entry')
        self.assertIn('lib.Entry', changed['main.pxl'])
        changed = self.rename(changed, 'lib.pxl', changed['lib.pxl'].index('value'), 'amount')
        self.assertIn('{amount: 1}', changed['main.pxl'])
        self.assertIn('row.amount', changed['main.pxl'])

    def test_rename_native_frame_function_parameter_and_flow_value(self):
        text = '''frame World = meters
fn Identity(point: Point3[World]) -> Point3[World] = point
flow Main() step(input: Point3[World]) -> Point3[World] {
 let item = Identity(input)
 output item
}
effect fn main() {}
'''
        changed = self.rename({'main.pxl': text}, 'main.pxl', text.index('World'), 'Scene')
        self.assertEqual(changed['main.pxl'], text.replace('World', 'Scene'))
        changed = self.rename({'main.pxl': text}, 'main.pxl', text.index('point:'), 'position')
        self.assertIn('Identity(input)', changed['main.pxl'])
        self.assertIn('Identity(position:', changed['main.pxl'])
        self.assertIn('= position', changed['main.pxl'])
        changed = self.rename({'main.pxl': text}, 'main.pxl', text.index('item ='), 'entity')
        self.assertEqual(changed['main.pxl'], text.replace('item', 'entity'))

    def test_rename_type_parameter_updates_native_type_references(self):
        text = 'fn identity[T](value: T) -> T = value\neffect fn main() { print(identity(1)) }'
        changed = self.rename({'main.pxl': text}, 'main.pxl', text.index('[T]') + 1, 'Value')
        self.assertEqual(changed['main.pxl'], text.replace('T', 'Value'))

    def test_rename_rejects_invalid_names_and_library_edits(self):
        from pixellang.model import PixelError
        text = 'effect fn main() { let value = 1; print(value) }'
        for name in ['if', 'new name', 'a; let injected', '', '1x']:
            with self.subTest(name=name), self.assertRaises(PixelError):
                self.rename({'main.pxl': text}, 'main.pxl', text.index('value'), name)
        files = {'main.pxl': 'import "lib.pxl" as lib\neffect fn main() { print(lib.value()) }',
                 'lib.pxl': 'export fn value() -> int = 1'}
        with self.assertRaisesRegex(PixelError, 'bundled library'):
            self.rename(files, 'main.pxl', files['main.pxl'].index('value'), 'renamed', editable_files=['main.pxl'])

    def test_rename_alias_updates_type_annotations_and_flow_targets(self):
        text = '''import "lib.pxl" as lib
flow Main() step(input: lib.Row) -> lib.Row {
 let item = lib.Identity(input)
 output item
}
effect fn main() {}
'''
        files = {'main.pxl': text, 'lib.pxl': 'export record Row { value: int }\nexport fn Identity(value: Row) -> Row = value'}
        changed = self.rename(files, 'main.pxl', text.index('as lib') + 3, 'models')
        self.assertEqual(changed['main.pxl'], text.replace('as lib', 'as models').replace('lib.', 'models.').replace('"models.pxl"', '"lib.pxl"'))

    def test_rename_untouched_shorthand_retains_both_bindings(self):
        text = 'record Row { value: int }\neffect fn main() { let value = 1; let row = Row{value}; print(row.value) }'
        changed = self.rename({'main.pxl': text}, 'main.pxl', text.index('row ='), 'result')
        self.assertEqual(changed['main.pxl'], text.replace('row', 'result'))

    def test_native_signature_ignores_nested_commas_and_string_delimiters(self):
        text = 'fn use(items: [int], text: string) {}\neffect fn main() { use([1, 2], "(,)", '
        result = self.index(text).signature_help('main.pxl', len(text))
        self.assertEqual(result['parameters'], ['items: [int]', 'text: string'])
        self.assertEqual(result['activeParameter'], 1)

    def test_native_signature_imported_generic_and_function_value(self):
        text = 'import "lib.pxl" as lib\neffect fn main() { lib.choose::[int](1, '
        index = self.index(text, **{'lib.pxl': 'export fn choose[T](first: T, second: T) -> T = first'})
        result = index.signature_help('main.pxl', len(text))
        self.assertEqual(result['label'], 'fn choose[T](first: T, second: T) -> T')
        self.assertEqual(result['activeParameter'], 1)
        text = 'effect fn main() { let add = fn(a: int, b: int) -> int = a + b; add(1, '
        result = self.index(text).signature_help('main.pxl', len(text))
        self.assertEqual(result['parameters'], ['arg1: int', 'arg2: int'])
        self.assertEqual(result['activeParameter'], 1)

    def test_native_signature_function_and_complete_source_cursor(self):
        text = 'frame World = meters\nfn Identity(point: Point3[World]) -> Point3[World] = point\neffect fn main() { let p = Point3::[World]{x: 0.0, y: 0.0, z: 0.0}; Identity(p) }'
        result = self.index(text).signature_help('main.pxl', text.index('Identity(p)') + len('Identity('))
        self.assertEqual(result['label'], 'fn Identity(point: Point3[World]) -> Point3[World]')

    def completions(self, source, **modules):
        offset = source.index('|')
        source = source.replace('|', '', 1)
        def analyze(snapshot):
            return self.index(snapshot['main.pxl'], **{p: t for p, t in snapshot.items() if p != 'main.pxl'})
        return self.index(source, **modules).complete('main.pxl', offset, analyze=analyze)

    def test_native_completion_nested_record_and_partial_member(self):
        result = self.completions('record Row { count: int, label: string }\neffect fn main() { let rows = [Row{count: 1, label: "a"}]\nrows[0].co|')
        self.assertEqual(result, [dict(label='count', kind='Field', detail='int')])

    def test_native_completion_scope_unfinished_and_future_declarations(self):
        result = self.completions('effect fn main() { let local = true\nif true { let inner = 2 }\nlo|')
        self.assertEqual(result, [dict(label='local', kind='Variable', detail='bool')])
        self.assertEqual(self.completions('fn helper() { let hidden = 1 }\neffect fn main() { hi|'), [])
        self.assertEqual(self.completions('effect fn main() { fut|; let future = 1 }'), [])

    def test_native_completion_only_exports_and_shadowed_module(self):
        library = 'export fn Public() -> int = 1\nfn private() -> int = 2\nexport record Row { field: int }'
        result = self.completions('import "lib.pxl"\neffect fn main() { lib.|', **{'lib.pxl': library})
        self.assertEqual([item['label'] for item in result], ['Public', 'Row'])
        result = self.completions('import "lib.pxl"\nrecord Row { field: int }\neffect fn main() { let lib = Row{field: 1}; lib.|', **{'lib.pxl': library})
        self.assertEqual(result, [dict(label='field', kind='Field', detail='int')])

    def test_native_completion_comments_strings_and_type_positions(self):
        self.assertEqual(self.completions('effect fn main() { print("text|") }'), [])
        self.assertEqual(self.completions('// val|'), [])
        for source in ['record Row { value: int }\nfn use(item: Ro|) {}',
                       'record Row { value: int }\neffect fn main() { let item: Ro| = 1 }',
                       'record Row { value: int }\nfn make() -> Ro| { return 1 }']:
            with self.subTest(source=source):
                self.assertEqual(self.completions(source), [dict(label='Row', kind='Struct', detail='record Row')])
        self.assertEqual(self.completions('effect fn main() { let count: in| = 1 }'),
                         [dict(label='int', kind='Keyword', detail='type')])
        result = self.completions('import "lib.pxl"\nfn use(x: lib.|) {}', **{'lib.pxl': 'export record Public { x: int }\nrecord private { x: int }\nfn Function() {}'})
        self.assertEqual([item['label'] for item in result], ['Public'])

    def test_native_completion_frame_and_inferred_generic_result(self):
        result = self.completions('frame World = meters\neffect fn main() { let value: Point3[Wo|] = Point3::[World]{x: 0.0, y: 0.0, z: 0.0} }')
        self.assertEqual(result, [dict(label='World', kind='Namespace', detail='frame World')])
        result = self.completions('record Row { value: int }\nfn identity[T](value: T) -> T = value\neffect fn main() { let row = identity(Row{value: 1}); row.|')
        self.assertEqual(result, [dict(label='value', kind='Field', detail='int')])

    def test_native_completion_loop_binding_does_not_escape_body(self):
        result = self.completions('effect fn main() { for entry in [1, 2] { en| } }')
        self.assertEqual([item['label'] for item in result], ['entry'])
        result = self.completions('effect fn main() { for entry in [1, 2] { print(entry) }; ent| }')
        self.assertNotIn('entry', [item['label'] for item in result])

    def test_workstation_native_language_service_end_to_end(self):
        from pixellang.workstation import Workstation
        service = self.compiler
        requests = []
        class InlineWorker:
            def request(self, request, **options):
                requests.append((request, options))
                return service.request(request, cancelled=options.get('cancelled', lambda: False))
            def close(self):
                pass
        studio = Workstation()
        studio.editor_compiler.worker.close()
        studio.editor_compiler.worker = InlineWorker()
        text = 'frame World = meters\nrecord Row { value: int }\nfn identity[T](value: T) -> T = value\neffect fn main() { let row = identity(Row{value: 1}); print(row.value) }'
        args = dict(files={'main.pxl': text}, source='main.pxl', offset=text.index('row.value'), revision=42)
        try:
            with ExitStack() as guards:
                for target in HOST_STAGES:
                    guards.enter_context(patch(target, side_effect=AssertionError('Host frontend used by Studio')))
                self.assertEqual(studio.request('hover', args)['label'], 'row: Row')
                count = len(requests)
                self.assertEqual(studio.request('hover', args)['label'], 'row: Row')
                self.assertEqual(len(requests), count)
                self.assertEqual(requests[-1][0]['revision'], 42)
                self.assertEqual([item['name'] for item in studio.request('document-symbols', args)], ['World', 'Row', 'identity', 'main'])
                location = studio.request('definition', dict(args, offset=text.index('row.value') + 4, with_sources=True))
                self.assertEqual(location['result']['start']['offset'], text.index('value: int'))
                self.assertIn('main.pxl', location['files'])
                self.assertEqual(len(studio.request('references', args)), 2)
                edits = studio.request('rename', dict(args, new_name='entry'))
                self.assertEqual(len(edits), 2)
                result = studio.request('completion', dict(args, offset=text.index('row.value') + 4))
                self.assertEqual(result, [dict(label='value', kind='Field', detail='int')])
                signature = studio.request('signature-help', dict(args, offset=text.index('identity(Row') + len('identity(')))
                self.assertEqual(signature['parameters'], ['value: T'])
                formatted = studio.request('format', args)
                self.assertEqual(formatted['files']['main.pxl'], self.format(text))
                with self.assertRaisesRegex(Exception, 'cancelled'):
                    studio.request('hover', args, cancelled=lambda: True)
                changed = text.replace('let row = identity(Row{value: 1})', 'let row = "text"').replace('row.value', 'row')
                self.assertEqual(studio.request('hover', dict(args, files={'main.pxl': changed}, offset=changed.rindex('row')))['label'], 'row: string')
        finally:
            studio.close()

    def test_workstation_format_is_syntax_only_and_atomic(self):
        from pixellang.workstation import Workstation
        service = self.compiler
        requests = []
        class InlineWorker:
            def request(self, request, **options):
                requests.append(request)
                return service.request(request, cancelled=options.get('cancelled', lambda: False))
            def close(self):
                pass
        studio = Workstation()
        studio.editor_compiler.worker.close()
        studio.editor_compiler.worker = InlineWorker()
        files = {'main.pxl': 'import "missing.pxl" as missing\neffect fn main(){\nmissing.use()\n}',
                 'detached.pxl': 'fn unused()->int=true'}
        before = dict(files)
        try:
            with patch.object(studio, 'symbol_index', side_effect=AssertionError('Formatting built a symbol index')):
                result = studio.request('format', dict(files=files, revision=73))
                self.assertEqual(result['files'], {name: self.format(text) for name, text in files.items()})
                self.assertEqual([r['operation'] for r in requests], ['format', 'format'])
                self.assertTrue(all(r['revision'] == 73 for r in requests))
                self.assertEqual(files, before)
                with self.assertRaises(Exception):
                    studio.request('format', dict(files={**files, 'broken.pxl': 'effect fn main('}))
                self.assertEqual(files, before)
                with self.assertRaisesRegex(Exception, 'cancelled'):
                    studio.request('format', dict(files=files), cancelled=lambda: True)
        finally:
            studio.close()

    def test_disconnected_files_keep_navigation_and_rename_references(self):
        files = {'main.pxl': 'effect fn main() { print(1) }',
                 'lib.pxl': 'export fn value() -> int = 42',
                 'other.pxl': 'import "lib.pxl"\nfn unused() -> int = lib.value()'}
        index = self.index(files['main.pxl'], **{p: t for p, t in files.items() if p != 'main.pxl'})
        self.assertEqual(index.diagnostics, [])
        self.assertEqual([item['name'] for item in index.document_symbols('other.pxl')], ['unused'])
        references = index.references('lib.pxl', files['lib.pxl'].index('value'))
        self.assertEqual({span['source'] for span in references}, {'lib.pxl', 'other.pxl'})
        changed = self.rename(files, 'lib.pxl', files['lib.pxl'].index('value'), 'answer')
        self.assertIn('lib.answer()', changed['other.pxl'])
        self.assertEqual(changed['main.pxl'], files['main.pxl'])

    def test_editor_roots_exclude_unused_library_without_hiding_imports(self):
        files = {'main.pxl': 'import "std/used.pxl" as used\neffect fn main() { print(used.value()) }',
                 'std/used.pxl': 'export fn value() -> int = 42',
                 'std/unused.pxl': 'invalid source that must not be parsed'}
        result = self.compiler.request(dict(version=1, id=1, operation='editor-index', entry='main.pxl',
            files=files, editorRoots=['main.pxl']))
        self.assertEqual(result['status'], 'ok', result)
        index = NativeSymbolIndex(files, 'main.pxl', result['result']['index'], editable_files=['main.pxl'])
        self.assertEqual(index.diagnostics, [])
        self.assertEqual(set(index.paths), {'main.pxl', 'std/used.pxl'})
        invalid = self.compiler.request(dict(version=1, id=2, operation='editor-index', entry='main.pxl',
            files=files, editorRoots=['missing.pxl']))
        self.assertEqual(invalid['status'], 'error')

    def test_native_enum_navigation_and_rename_across_modules(self):
        library = 'export enum Event[T] { Stop, Data(value: T) }'
        text = '''import "model.pxl" as model
effect fn main() {
 let event = model.Event::[int].Data(42)
 print(match event { model.Event.Stop => 0, model.Event.Data(value) => value })
}
'''
        files = {'main.pxl': text, 'model.pxl': library}
        index = self.index(text, **{'model.pxl': library})
        self.assertEqual(index.diagnostics, [])
        for token in ['Data(42)', 'Data(value)']:
            definition = index.definition('main.pxl', text.index(token))
            self.assertEqual(definition['source'], 'model.pxl')
            self.assertEqual(definition['start']['offset'], library.index('Data'))
        for source, offset, name, old in [('main.pxl', text.index('Data(42)'), 'Payload', 'Data'),
                                          ('model.pxl', library.index('Event'), 'Message', 'Event')]:
            changed = self.rename(files, source, offset, name)
            self.assertEqual(changed, {path: value.replace(old, name) for path, value in files.items()})
        changed = self.rename(files, 'model.pxl', library.index('T]'), 'Item')
        self.assertEqual(changed['model.pxl'], library.replace('T', 'Item'))
        changed = self.rename(files, 'main.pxl', text.index('as model') + 3, 'types')
        self.assertEqual(changed['main.pxl'], text.replace('as model', 'as types').replace('model.', 'types.').replace('"types.pxl"', '"model.pxl"'))

    def test_native_enum_scoped_patterns_and_collision_rejection(self):
        from pixellang.model import PixelError
        text = '''enum First { Data(value: int), Stop }
enum Second { Data(value: int) }
effect fn main() {
 let value = 9
 print(match First.Data(1) { First.Data(value) => value, First.Stop => 0 })
 match Second.Data(2) { Second.Data(value) => { print(value) } }
 print(value)
}
'''
        files = {'main.pxl': text}
        changed = self.rename(files, 'main.pxl', text.index('Data'), 'Item')['main.pxl']
        self.assertIn('enum First { Item', changed)
        self.assertIn('First.Item(value)', changed)
        self.assertIn('Second.Data(value)', changed)
        changed = self.rename(files, 'main.pxl', text.index('value ='), 'outside')['main.pxl']
        self.assertIn('let outside = 9', changed)
        self.assertIn('print(outside)', changed)
        self.assertIn('First.Data(value) => value', changed)
        with self.assertRaises(PixelError):
            self.rename(files, 'main.pxl', text.index('Data'), 'Stop')

    def test_native_enum_generic_constructor_and_pattern_completion(self):
        self.assertEqual(self.completions('effect fn main(){match Option.Some(1){So|'), [])
        cases = [
            ('enum Event[T] { Stop, Data(value: T) }\neffect fn main() { let e = Event::[int].D|', 'Data', 'Data(value: int)'),
            ('effect fn main() { let e = Option::[int].S|', 'Some', 'Some(value: int)'),
            ('effect fn main() { match Option.Some(1) { .So|', 'Some', 'Some(value: int)'),
            ('enum Event { Stop, Data(value: int) }\neffect fn main() { match Event.Stop { Event.D|', 'Data', 'Data(value: int)'),
        ]
        for source, name, detail in cases:
            with self.subTest(source=source):
                self.assertEqual(self.completions(source), [dict(label=name, kind='EnumMember', detail=detail)])
        for source, name, detail in [
            ('enum Event { Stop, Data(value:int) }\neffect fn main(){match (Event.Stop,Option.Some(1)){(.Stop,.So|', 'Some', 'Some(value: int)'),
            ('enum Inner { Empty, Item(value:int) }\nenum Outer { Wrap(value:Inner) }\neffect fn main(){match Outer.Wrap(Inner.Empty){.Wrap(.I|', 'Item', 'Item(value: int)'),
        ]:
            with self.subTest(source=source):
                self.assertEqual(self.completions(source), [dict(label=name,kind='EnumMember',detail=detail)])
        result = self.completions('import "model.pxl" as model\neffect fn main() { let e = model.Event::[string].D|',
                                  **{'model.pxl': 'export enum Event[T] { Data(value: T) }'})
        self.assertEqual(result, [dict(label='Data', kind='EnumMember', detail='Data(value: string)')])

    def test_native_spatial_fields_and_builtin_type_completion(self):
        source = 'frame World = meters\neffect fn main() { let point = Point3::[World]{x: 0.0, y: 1.0, z: 2.0}; point.| }'
        result = self.completions(source)
        self.assertEqual(result, [dict(label='distance',kind='Method',detail='fn distance(other: Point3[World]) -> float64')] + [dict(label=name, kind='Field', detail='float64') for name in ['x', 'y', 'z']])
        source = source.replace('point.|', 'print(point.z)')
        self.assertEqual(self.index(source).hover('main.pxl', source.index('point.z') + 6)['label'], 'z: float64')
        for name in ['Point3', 'Vec3', 'Transform3', 'Space', 'Entity', 'Link', 'Flow', 'Option']:
            with self.subTest(name=name):
                result = self.completions('effect fn main() { let value: ' + name[:2] + '| }')
                self.assertIn(dict(label=name, kind='Keyword', detail='type'), result)

    def test_native_generic_record_fields_and_type_parameter_identity(self):
        text = 'record Box[T] { value: T }\neffect fn main() { let box = Box::[int]{value: 1}; print(box.value) }'
        self.assertEqual(self.index(text).hover('main.pxl', text.index('box.value') + 4)['label'], 'value: int')
        changed = self.rename({'main.pxl': text}, 'main.pxl', text.index('T]'), 'Item')
        self.assertEqual(changed['main.pxl'], text.replace('T', 'Item'))
        result = self.completions(text.replace('print(box.value)', 'box.va|'))
        self.assertEqual(result, [dict(label='value', kind='Field', detail='int')])
        result = self.completions('record Box[Element] { value: El| }\neffect fn main() {}')
        self.assertEqual(result, [dict(label='Element', kind='TypeParameter', detail='type parameter')])

    def test_native_flow_value_and_output_projection_completion(self):
        text = '''record Row { amount: int }
fn Identity(value: Row) -> Row = value
flow Main() step(input: Row) -> int {
 let item = Identity(inp|ut)
 output item.amount
}
effect fn main() {}
'''
        self.assertEqual(self.completions(text), [dict(label='input', kind='Variable', detail='Row')])
        text = text.replace('inp|ut', 'input').replace('item.amount', 'item.am|ount')
        self.assertEqual(self.completions(text), [dict(label='amount', kind='Field', detail='int')])
        text = text.replace('am|ount', 'amount').replace('item.amount', 'it|em.amount')
        result = self.completions(text)
        self.assertEqual([item['label'] for item in result], ['item'])

    def test_flow_completion_separates_creation_and_step_scopes(self):
        prefix = 'flow Counter(initialSeed:int) step(stepDelta:int)->int{'
        self.assertEqual([item['label'] for item in self.completions(
            prefix+'state total=initialS|eed;next total=total+stepDelta;output total}\neffect fn main(){}')], ['initialSeed'])
        self.assertEqual(self.completions(
            prefix+'state total=stepD|elta;next total=total+stepDelta;output total}\neffect fn main(){}'), [])
        self.assertEqual([item['label'] for item in self.completions(
            prefix+'state total=initialSeed;next total=total+stepD|elta;output total}\neffect fn main(){}')], ['stepDelta'])
        self.assertEqual([item['label'] for item in self.completions(
            prefix+'state total=initialSeed;next total=total+stepDelta;output initialS|eed}\neffect fn main(){}')], ['initialSeed'])
        self.assertEqual([item['label'] for item in self.completions(
            'flow Config(initialSeed:int) step()->int{output initialS|eed}\neffect fn main(){}')], ['initialSeed'])

    def test_explicit_option_constructors_compile_and_execute_natively(self):
        from pixellang.vm import VM
        source = '''effect fn main() {
 let value = Option::[int].Some(42)
 let missing = Option::[int].None
 print(match value { Option.Some(n) => n, Option.None => 0 })
 print(match missing { Option.Some(n) => n, Option.None => -1 })
}
'''
        with ExitStack() as guards:
            for target in HOST_STAGES:
                guards.enter_context(patch(target, side_effect=AssertionError('Host frontend used')))
            compiled = self.compiler.request(dict(version=1, id=1, operation='compile', entry='main.pxl', files={'main.pxl': source}))
            self.assertEqual(compiled['status'], 'ok', compiled)
            self.assertEqual(VM(compiled['result']['bytecode']).run(), [42, -1])
            exported = self.compiler.request(dict(version=1, id=2, operation='export-png', entry='main.pxl', files={'main.pxl': source}))
            self.assertEqual(exported['status'], 'ok', exported)
            restored = self.compiler.request(dict(version=1, id=3, operation='compile-png', image=exported['result']['image']))
            self.assertEqual(restored['status'], 'ok', restored)
            self.assertEqual(VM(restored['result']['bytecode']).run(), [42, -1])

    def test_native_rename_rejects_capture_of_builtin_reference(self):
        from pixellang.model import PixelError
        text = 'effect fn main() { let count = fn(items: [int]) -> int = 0; print(len([1])) }'
        with self.assertRaisesRegex(PixelError, 'binding'):
            self.rename({'main.pxl': text}, 'main.pxl', text.index('count'), 'len')

    def test_native_result_binding_hover_completion_and_rename(self):
        text = 'effect fn main() { match parseInt("invalid") { .Ok(value) => { print(value) }, .Err(error) => { print(error.message) } } }'
        index = self.index(text)
        self.assertEqual(index.diagnostics, [])
        self.assertEqual(index.definition('main.pxl', text.index('error.message'))['start']['offset'], text.index('.Err(error)') + 5)
        self.assertEqual(index.hover('main.pxl', text.index('message'))['label'], 'message: string')
        changed = self.rename({'main.pxl': text}, 'main.pxl', text.index('.Err(error)') + 5, 'problem')['main.pxl']
        self.assertEqual(changed, text.replace('.Err(error)', '.Err(problem)').replace('error.message', 'problem.message'))
        items = self.completions(text.replace('error.message', 'error.me|ssage'))
        self.assertEqual(items, [dict(label='message', kind='Field', detail='string')])
