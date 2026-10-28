"""Native diagnostics name source types and preserve explicit permissions."""
import json
import unittest

from pixellang.code_image import CodeImage
from pixellang.vm import VM
from tests.native_compiler import load_native_compiler



class NativeTypeDiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CodeImage(load_native_compiler(driver=None))

    def diagnostics(self, files):
        if isinstance(files, str):
            files = {'main.pxl': files}
        events = VM(self.compiler, input_text=json.dumps(dict(source="main.pxl", text="", operation="check", files=files)),
                    max_heap_items=16_000_000, max_heap_objects=1_000_000,
                    max_steps=10_000_000).run()
        return events[0]['diagnostics']

    def test_imported_types_in_constructor_variable_and_return_contexts(self):
        for body in [
            'fn main(){consume(Other{value:1})}',
            'fn main(){let other=Other{value:1};consume(other)}',
            'fn wrong()->models.Row {return Other{value:1}}\nfn main(){}',
        ]:
            with self.subTest(body=body):
                result = self.diagnostics({
                    'models.pxl': 'export record Row {value:int}',
                    'main.pxl': 'import "models.pxl" as models\nrecord Other {value:int}\nfn consume(x:models.Row){}\n' + body,
                })
                self.assertTrue(any('Expected models.Row, got Other' in d['message'] for d in result), result)
                self.assertTrue(all(d['source'] == 'main.pxl' for d in result), result)

    def test_identical_short_names_are_qualified_by_import_alias(self):
        result = self.diagnostics({
            'a.pxl': 'export record Row {value:int}',
            'b.pxl': 'export record Row {value:int}',
            'main.pxl': 'import "a.pxl" as left\nimport "b.pxl" as right\nfn consume(x:left.Row){}\nfn main(){let row=right.Row{value:1};consume(row)}',
        })
        self.assertTrue(any('Expected left.Row, got right.Row' in d['message'] for d in result), result)

    def test_explicit_write_permissions_and_generic_result_evidence(self):
        cases = [
            ('fn use(x:inout [int]){}\nfn main(){var values=[1];use(values)}', 'inout arguments must match an explicit & access marker'),
            ('fn use(x:inout [int]){}\nfn main(){let values=[1];use(&values)}', 'Cannot update a value through a readonly binding'),
            ('fn identity[T](value:T)->T=value\nfn main(){let text:string=identity(1)}', 'Expected string, got int'),
        ]
        for source, message in cases:
            with self.subTest(source=source):
                result = self.diagnostics(source)
                self.assertTrue(any(message in d['message'] for d in result), result)

    def test_recursive_record_names_remain_finite_and_valid_programs_pass(self):
        result = self.diagnostics('record Node{next:Option[Node]}\nfn use(x:Node){}\nfn main(){use(1)}')
        self.assertTrue(any('Expected Node, got int' in d['message'] for d in result), result)
        self.assertEqual(self.diagnostics('fn use(x:inout [int]){x.append(2)}\nfn main(){var x=[1];use(&x)}'), [])
