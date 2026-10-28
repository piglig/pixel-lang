"""Native syntax and name-resolution boundaries across source, images and tools."""
from tests.native_compiler import native_service
import unittest
from unittest.mock import patch

from pixellang.codec import rgba
from pixellang.model import PixelError
from pixellang.native_editor import NativeSymbolIndex
from pixellang.vm import VM

GLOBAL_NAMES = (
    'spaceNew', 'spaceInsert', 'spaceGet', 'spaceMove', 'spaceRemove', 'spaceNeighbors',
    'spaceAhead', 'spaceSize', 'entityPosition', 'entityPayload', 'entityId',
    'distance3', 'dot3', 'transformPoint', 'transformVector', 'transformThen',
    'spaceConnect', 'spaceDisconnect', 'spaceGetLink', 'spaceSetLinkPayload',
    'spaceOutgoing', 'spaceIncoming', 'spaceLinkCount', 'linkId', 'linkPayload',
    'linkSource', 'linkTarget',
)

class NativeLanguageBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = native_service()
        cls.addClassCleanup(cls.compiler.close)

    def request(self, operation, **args):
        with patch('pixellang.text.parse_text', side_effect=AssertionError('host frontend forbidden')):
            return self.compiler.request(dict(version=1, operation=operation, incremental=False, **args))

    def result(self, operation, **args):
        response = self.request(operation, **args)
        self.assertEqual(response['status'], 'ok', response)
        return response['result']

    def compile(self, source):
        return self.request('compile', files={'main.pxl': source})

    def test_old_syntax_and_builtin_have_source_diagnostics(self):
        for body in ('try {print(1)} catch error {print(error)}', 'fail("bad")'):
            with self.subTest(body=body):
                response=self.request('compile',files={'main.pxl':'effect fn main(){'+body+'}'})
                self.assertEqual(response['status'],'diagnostics',response)
                self.assertNotIn('bytecode',response.get('result') or {})

    def test_nested_named_functions_are_rejected_during_check(self):
        for body in ('fn local()->int=1', 'effect fn local(){print(1)}',
                     'if true {fn local()->int=1}', 'let outer=fn(){fn local()->int=1}'):
            source = 'effect fn main(){' + body + '}'
            for operation in ('check', 'compile', 'export-png'):
                with self.subTest(body=body, operation=operation):
                    response = self.request(operation, files={'main.pxl': source})
                    self.assertEqual(response['status'], 'diagnostics', response)
                    self.assertTrue(any(item['phase'] == 'check' and 'module level' in item['message']
                                        for item in response['diagnostics']), response)
        source = 'effect fn main(){var value=1;let read=fn()->int=value;value=2;print(read())}'
        response = self.compile(source)
        self.assertEqual(response['status'], 'ok', response)
        self.assertEqual(VM(response['result']['bytecode']).run(), [1])

    def test_user_names_roundtrip_through_native_png(self):
        source='''fn fail(value:int)->int=value+1
effect fn main(){let try=fail(6);let catch=try;print(catch)}'''
        def result(operation, **args):
            response=self.request(operation,**args)
            self.assertEqual(response['status'],'ok',response)
            return response['result']
        files={'main.pxl':source}
        original=result('compile',files=files)['bytecode']
        self.assertEqual(VM(original).run(),[7])
        image=result('export-png',files=files)['image']
        self.assertEqual(VM(result('compile-png',image=image)['bytecode']).run(),[7])
        recovered=result('recover-png',image=image)
        self.assertEqual(VM(result('compile',**recovered)['bytecode']).run(),[7])

    def test_same_named_function_cannot_hide_missing_return(self):
        response=self.request('compile',files={'main.pxl':'fn fail(){}\nfn incomplete()->int{fail()}\nfn main(){}'})
        self.assertEqual(response['status'],'diagnostics',response)

    def test_global_operations_are_unknown_names(self):
        for name in GLOBAL_NAMES:
            with self.subTest(name=name):
                response = self.request('compile', files={'main.pxl': f'fn main(){{{name}()}}'})
                self.assertEqual(response['status'], 'diagnostics', response)
                self.assertTrue(any('Unknown name' in item['message'] and name in item['message']
                                    for item in response['diagnostics']), response)
                self.assertNotIn('bytecode', response.get('result') or {})

    def test_user_defined_names_execute_and_roundtrip_through_png(self):
        source = '\n'.join(f'fn {name}()->int={i}' for i, name in enumerate(GLOBAL_NAMES))
        source += '\neffect fn main(){' + ';'.join(f'print({name}())' for name in GLOBAL_NAMES) + '}'
        files = {'main.pxl': source}
        expected = list(range(len(GLOBAL_NAMES)))
        self.assertEqual(VM(self.result('compile', files=files)['bytecode']).run(), expected)
        image = self.result('export-png', files=files)['image']
        self.assertEqual(VM(self.result('compile-png', image=image)['bytecode']).run(), expected)
        recovered = self.result('recover-png', image=image)
        self.assertEqual(VM(self.result('compile', **recovered)['bytecode']).run(), expected)

    def test_editor_does_not_offer_retired_builtins(self):
        def analyze(files):
            result = self.result('editor-index', files=files)
            return NativeSymbolIndex(files, 'main.pxl', result['index'])
        for prefix in ('space', 'entity', 'link', 'distance', 'dot', 'transform'):
            with self.subTest(prefix=prefix):
                source = 'fn main(){' + prefix
                files = {'main.pxl': source}
                items = analyze(files).complete('main.pxl', len(source), analyze=analyze)
                labels = {item['label'] for item in items}
                self.assertFalse(labels.intersection(GLOBAL_NAMES), labels)

    def test_increment_and_decrement_have_native_source_diagnostics(self):
        for expression in ('value++', 'value--', '++value', 'items[0]++'):
            with self.subTest(expression=expression):
                source = 'effect fn main(){var value=0;var items=[1];' + expression + '}'
                response = self.request('compile', files={'main.pxl': source})
                self.assertEqual(response['status'], 'diagnostics', response)
                self.assertNotIn('bytecode', response.get('result') or {})

    def test_explicit_updates_and_double_negation_survive_png(self):
        source = '''effect fn main(){
 var value=3
 value+=2
 value-=1
 print(value)
 print(--value)
}'''
        files = {'main.pxl': source}
        self.assertEqual(VM(self.result('compile', files=files)['bytecode']).run(), [4, 4])
        image = self.result('export-png', files=files)['image']
        self.assertEqual(VM(self.result('compile-png', image=image)['bytecode']).run(), [4, 4])
        recovered = self.result('recover-png', image=image)
        self.assertEqual(VM(self.result('compile', **recovered)['bytecode']).run(), [4, 4])

    def test_undeclared_conversion_names_are_not_builtins(self):
        for call in ('freeze([1])', 'thaw([1])', 'thaw[[int]]([1])'):
            with self.subTest(call=call):
                result = self.compile('fn main() { let value=' + call + ' }')
                self.assertEqual(result['status'], 'diagnostics', result)
                self.assertTrue(result['diagnostics'])

    def test_same_named_user_functions_execute_as_ordinary_functions(self):
        result = self.compile('''fn freeze[T](value:T)->T=value
fn thaw[T](value:T)->T=value
effect fn main(){print(thaw::[int](freeze(7)))}''')
        self.assertEqual(result['status'], 'ok', result)
        code = result['result']['bytecode']
        self.assertEqual(VM(code).run(), [7])
        self.assertNotIn('FREEZE', str(code))
        self.assertNotIn('THAW', str(code))

    def test_nested_value_edits_preserve_input_without_conversions(self):
        result = self.compile('''record Box { values:[int] }
fn edit(box:inout Box){box.values.append(2)}
effect fn main(){
 let original=Box{values:[1]}
 var copy=original
 edit(&copy)
 print(original.values);print(copy.values)
}''')
        self.assertEqual(result['status'], 'ok', result)
        self.assertEqual(VM(result['result']['bytecode']).run(), [[1], [1, 2]])


class BuiltinEncodingBoundaryTests(unittest.TestCase):
    def test_conversion_builtin_pixels_cannot_be_encoded(self):
        for name in ('freeze', 'thaw'):
            with self.subTest(name=name), self.assertRaises(PixelError):
                rgba('builtin', name)
