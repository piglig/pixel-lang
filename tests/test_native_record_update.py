"""Record updates compile natively without a source-to-source compatibility pass."""
from tests.native_compiler import native_service
import unittest
from pixellang.vm import VM


class NativeRecordUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = native_service()
        cls.addClassCleanup(cls.compiler.close)

    def compile(self, source):
        return self.compiler.request(dict(version=1, operation='compile', files={'main.pxl': source}))

    def test_order_single_evaluation_and_preserved_fields(self):
        response = self.compile('''record Order { status: int, total: int, note: string }
effect fn original() -> Order { print("base"); return Order{status: 0, total: 7, note: "keep"} }
effect fn amount() -> int { print("total"); return 10 }
effect fn status() -> int { print("status"); return 2 }
effect fn main() {
 let before = original()
 let after = before with { total: amount(), status: status() }
 print(before.status); print(before.total)
 print(after.status); print(after.total); print(after.note)
 let total = 12
 print((after with { total }).total)
 print((after with {}).note)
}''')
        self.assertEqual(response['status'], 'ok', response)
        self.assertEqual(VM(response['result']['bytecode']).run(),
                         ['base', 'total', 'status', 0, 7, 2, 10, 'keep', 12, 'keep'])

    def test_rejects_invalid_record_updates(self):
        for expression in ('order with { missing: 1 }',
                           'order with { status: 1, status: 2 }',
                           'order with { status: "bad" }',
                           '1 with { status: 1 }'):
            with self.subTest(expression=expression):
                response = self.compile('record Order { status: int }\neffect fn main() { let order = Order{status: 0}; let updated = ' + expression + ' }')
                self.assertNotEqual(response['status'], 'ok', response)

    def test_png_execution_and_recovered_source_preserve_update(self):
        source = '''record Item { value: int, label: string }
effect fn original() -> Item { print("once"); return Item{value: 1, label: "kept"} }
effect fn main() {
 let item = original() with { value: 3 }
 print(item.value); print(item.label)
}'''
        exported = self.compiler.request(dict(version=1, operation='export-png', files={'main.pxl': source}))
        self.assertEqual(exported['status'], 'ok', exported)
        image = exported['result']['image']
        compiled = self.compiler.request(dict(version=1, operation='compile-png', image=image))
        self.assertEqual(compiled['status'], 'ok', compiled)
        self.assertEqual(VM(compiled['result']['bytecode']).run(), ['once', 3, 'kept'])
        recovered = self.compiler.request(dict(version=1, operation='recover-png', image=image))
        self.assertEqual(recovered['status'], 'ok', recovered)
        self.assertTrue(any(' with {' in text for text in recovered['result']['files'].values()))
        rebuilt = self.compiler.request(dict(version=1, operation='compile', **recovered['result']))
        self.assertEqual(rebuilt['status'], 'ok', rebuilt)
        self.assertEqual(VM(rebuilt['result']['bytecode']).run(), ['once', 3, 'kept'])


if __name__ == '__main__':
    unittest.main()
