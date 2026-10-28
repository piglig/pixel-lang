"""Ordinary closures capture readonly values, including nested value containers."""
from tests.native_compiler import native_service
import unittest
from pixellang.vm import VM


class NativeValueCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = native_service()
        cls.addClassCleanup(cls.compiler.close)

    def compile(self, source):
        return self.compiler.request(dict(version=1, operation='compile', files={'main.pxl': source}))

    def test_outer_updates_do_not_change_captured_values(self):
        response = self.compile('''effect fn main() {
 var number = 2
 var values = [1]
 let readNumber = fn() -> int = number
 let readValues = fn() -> [int] = values
 number = 9
 values[0] = 8
 print(readNumber()); print(readValues())
 var copy = readValues()
 copy.append(3)
 print(copy); print(readValues())
}''')
        self.assertEqual(response['status'], 'ok', response)
        self.assertEqual(VM(response['result']['bytecode']).run(), [2, [1], [1, 3], [1]])

    def test_captured_writes_are_rejected(self):
        bodies = [
            'var x = 1; let f = fn() { x = 2 }; f()',
            'var x = [1]; let f = fn() { x[0] = 2 }; f()',
            'var x = [1]; let f = fn() { x.append(2) }; f()',
        ]
        for body in bodies:
            with self.subTest(body=body):
                response = self.compile('effect fn main() { ' + body + ' }')
                self.assertNotEqual(response['status'], 'ok', response)
                self.assertIn('Captured values are readonly', str(response))

    def test_nested_closures_capture_the_current_value(self):
        response = self.compile('''fn make(value: int) -> fn() -> fn() -> int {
 return fn() -> fn() -> int = fn() -> int = value
}
effect fn main() {
 let first = make(3)
 let second = make(7)
 print(first()()); print(second()())
}''')
        self.assertEqual(response['status'], 'ok', response)
        self.assertEqual(VM(response['result']['bytecode']).run(), [3, 7])

    def test_png_execution_and_recovered_source_keep_capture_values(self):
        source = '''effect fn main() {
 var values = [1]
 let read = fn() -> [int] = values
 values.append(2)
 print(read()); print(values)
}'''
        exported = self.compiler.request(dict(version=1, operation='export-png', files={'main.pxl': source}))
        self.assertEqual(exported['status'], 'ok', exported)
        image = exported['result']['image']
        compiled = self.compiler.request(dict(version=1, operation='compile-png', image=image))
        self.assertEqual(compiled['status'], 'ok', compiled)
        self.assertEqual(VM(compiled['result']['bytecode']).run(), [[1], [1, 2]])
        recovered = self.compiler.request(dict(version=1, operation='recover-png', image=image))
        self.assertEqual(recovered['status'], 'ok', recovered)
        rebuilt = self.compiler.request(dict(version=1, operation='compile', **recovered['result']))
        self.assertEqual(rebuilt['status'], 'ok', rebuilt)
        self.assertEqual(VM(rebuilt['result']['bytecode']).run(), [[1], [1, 2]])

    def test_each_iteration_captures_its_own_value(self):
        response = self.compile('''effect fn main() {
 var callbacks: [fn() -> int] = []
 for item in [1, 2, 3] {
  var value = item
  callbacks.append(fn() -> int = value)
  value = 9
 }
 for callback in callbacks { print(callback()) }
}''')
        self.assertEqual(response['status'], 'ok', response)
        self.assertEqual(VM(response['result']['bytecode']).run(), [1, 2, 3])
