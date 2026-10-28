"""Ordinary var values update locally, preserving aliases and evaluation order."""
from tests.native_compiler import native_service
import unittest
from pixellang.vm import VM
from pixellang.model import PixelError


class NativeValueAssignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = native_service()
        cls.addClassCleanup(cls.compiler.close)

    def compile(self, source):
        return self.compiler.request(dict(version=1, operation='compile', files={'main.pxl': source}))

    def execute(self, source):
        response = self.compile(source)
        self.assertEqual(response['status'], 'ok', response)
        return VM(response['result']['bytecode']).run()

    def test_nested_records_arrays_and_maps_do_not_mutate_aliases(self):
        self.assertEqual(self.execute('''record Row { items: [int], count: int }
effect fn main() {
 let original = Row{items: [1, 2], count: 0}
 var edited = original
 edited.items[0] = 9
 edited.count += 2
 edited.items[1] += 1
 print(original.items); print(original.count)
 print(edited.items); print(edited.count)
 let old = map[string, [int]]{"a": [1, 2]}
 var changed = old
 changed["a"][1] = 8
 changed["b"] = [7]
 print(old); print(changed)
}'''), [[1, 2], 0, [9, 3], 2, {'a': [1, 2]}, {'a': [1, 8], 'b': [7]}])

    def test_index_expressions_evaluate_once_before_rhs_and_compound_reads(self):
        self.assertEqual(self.execute('''effect fn first() -> int { print("first"); return 0 }
effect fn second() -> int { print("second"); return 1 }
effect fn rhs() -> int { print("rhs"); return 5 }
effect fn main() {
 var values = [[1, 2]]
 values[first()][second()] += rhs()
 print(values)
}'''), ['first', 'second', 'rhs', [[1, 7]]])

    def test_readonly_bindings_parameters_and_temporary_roots_are_rejected(self):
        for source in [
            'effect fn main() { let x = [1]; x[0] = 2 }',
            'fn change(x: [int]) { x[0] = 2 }\neffect fn main() {}',
            'fn values() -> [int] = [1]\neffect fn main() { values()[0] = 2 }',
        ]:
            with self.subTest(source=source):
                self.assertNotEqual(self.compile(source)['status'], 'ok')

    def test_invalid_address_stops_before_later_indices_and_rhs(self):
        for first, second, expected in [(3, 0, ['first']), (0, 3, ['first', 'second'])]:
            source = '''effect fn first() -> int { print("first"); return FIRST }
effect fn second() -> int { print("second"); return SECOND }
effect fn rhs() -> int { print("rhs"); return 5 }
effect fn main() { var values = [[1]]; values[first()][second()] = rhs() }
'''.replace('FIRST', str(first)).replace('SECOND', str(second))
            with self.subTest(first=first, second=second):
                response = self.compile(source)
                self.assertEqual(response['status'], 'ok', response)
                vm = VM(response['result']['bytecode'])
                with self.assertRaises(PixelError):
                    vm.run()
                self.assertEqual(vm.output, expected)

    def test_png_execution_and_recovery_keep_value_assignments(self):
        source = '''record Row { items: [int] }
effect fn main() {
 let original = Row{items: [1]}
 var changed = original
 changed.items[0] = 7
 print(original.items); print(changed.items)
}'''
        exported = self.compiler.request(dict(version=1, operation='export-png', files={'main.pxl': source}))
        self.assertEqual(exported['status'], 'ok', exported)
        image = exported['result']['image']
        delivered = self.compiler.request(dict(version=1, operation='compile-png', image=image))
        self.assertEqual(delivered['status'], 'ok', delivered)
        self.assertEqual(VM(delivered['result']['bytecode']).run(), [[1], [7]])
        recovered = self.compiler.request(dict(version=1, operation='recover-png', image=image))
        self.assertEqual(recovered['status'], 'ok', recovered)
        rebuilt = self.compiler.request(dict(version=1, operation='compile', **recovered['result']))
        self.assertEqual(rebuilt['status'], 'ok', rebuilt)
        self.assertEqual(VM(rebuilt['result']['bytecode']).run(), [[1], [7]])


if __name__ == '__main__':
    unittest.main()
