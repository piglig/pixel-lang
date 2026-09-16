import unittest

from pixellang.model import PixelError
from pixellang.project import compile_project
from pixellang.vm import VM


class BulkArrayTests(unittest.TestCase):
    def execute(self, body, **limits):
        code = compile_project({'main.pxl': 'fn main() {' + body + '}'})[1].bytecode
        vm = VM(code, **limits)
        output = vm.run()
        self.assertEqual(vm.heap_items, sum(len(obj['items']) for obj in vm.heap.values()))
        return output

    def test_extend_self_and_return_alias(self):
        self.assertEqual(self.execute('let a = [1, 2]; let b = extend(a, a); b[0] = 9; print(a); print(b)'),
                         [[9, 2, 1, 2], [9, 2, 1, 2]])

    def test_repeat_copies_array_but_preserves_element_references(self):
        self.assertEqual(self.execute('let a = [[1]]; let b = arrayRepeat(a, 2); append(b, [3]); b[0][0] = 7; print(a); print(b); print(arrayRepeat(a, 0))'),
                         [[[7]], [[7], [7], [3]], []])

    def test_limits_before_allocation_and_negative_count(self):
        for expression in ('arrayRepeat([1], 9223372036854775807)', 'extend([1, 2], [3, 4])'):
            with self.subTest(expression=expression), self.assertRaisesRegex(PixelError, 'Heap budget exceeded'):
                self.execute(expression, max_heap_items=4)
        self.assertEqual(self.execute('try { arrayRepeat([1], -1) } catch err { print(err.code) }'),
                         ['collection.index_bounds'])
        self.assertEqual(self.execute('print(arrayRepeat(([]: [int]), 9223372036854775807))'), [[]])

    def test_static_types(self):
        for expression in ('extend([1], [true])', 'extend("x", "y")', 'arrayRepeat([1], true)'):
            with self.subTest(expression=expression), self.assertRaises(PixelError):
                self.execute(expression)

    def test_collection_keeps_bulk_arguments_and_nested_references_alive(self):
        self.assertEqual(self.execute('arrayRepeat([0], 4); let a = [[7]]; extend(a, a); print(a)',
                                      max_heap_items=7), [[[7], [7]]])


if __name__ == '__main__':
    unittest.main()
