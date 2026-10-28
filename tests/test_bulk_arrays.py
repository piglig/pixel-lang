import unittest

from pixellang.model import PixelError
from pixellang.compiler_client import CompilerClient
from pixellang.vm import VM


class BulkArrayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def execute(self, body, **limits):
        result = self.compiler.request('compile', files={'main.pxl': 'effect fn main() {' + body + '}'}, source='main.pxl')
        self.assertEqual(result['status'], 'ok', result)
        code = result['result']['bytecode']
        vm = VM(code, **limits)
        output = vm.run()
        self.assertEqual(vm.heap_items, sum(len(obj['items']) for obj in vm.heap.values()))
        return output

    def test_extend_self_and_copy_isolation(self):
        self.assertEqual(self.execute('var a = [1, 2]; a.extend(a); var b = a; b[0] = 9; print(a); print(b)'),
                         [[1, 2, 1, 2], [9, 2, 1, 2]])

    def test_repeat_and_nested_updates_preserve_other_values(self):
        self.assertEqual(self.execute('let a = [[1]]; var b = arrayRepeat(a, 2); b.append([3]); b[0][0] = 7; print(a); print(b); print(arrayRepeat(a, 0))'),
                         [[[1]], [[7], [1], [3]], []])

    def test_limits_before_allocation_and_negative_count(self):
        for expression in ('arrayRepeat([1], 9223372036854775807)', 'var a = [1, 2]; a.extend([3, 4])'):
            with self.subTest(expression=expression), self.assertRaisesRegex(PixelError, 'Heap budget exceeded'):
                self.execute(expression, max_heap_items=4)
        with self.assertRaisesRegex(PixelError, 'nonnegative') as caught:
            self.execute('arrayRepeat([1], -1)')
        self.assertEqual(caught.exception.code, 'collection.index_bounds')
        self.assertEqual(self.execute('print(arrayRepeat(([]: [int]), 9223372036854775807))'), [[]])

    def test_static_types(self):
        for expression in ('var a = [1]; a.extend([true])', 'var text = "x"; text.extend("y")', 'arrayRepeat([1], true)'):
            with self.subTest(expression=expression), self.assertRaises(PixelError):
                self.execute(expression)

    def test_collection_keeps_bulk_arguments_and_nested_references_alive(self):
        self.assertEqual(self.execute('arrayRepeat([0], 4); var a = [[7]]; a.extend(a); print(a)',
                                      max_heap_items=7), [[[7], [7]]])


if __name__ == '__main__':
    unittest.main()
