"""Generic capability contracts survive checking, inference and pixel delivery."""
from tests.native_compiler import native_service
import unittest
from pathlib import Path
from pixellang.vm import VM

SOURCE = '''enum State { Waiting, Paid(amount: int) }
record Box[T: Equatable] { value: T }
fn Contains[T: Equatable](values: [T], target: T) -> bool {
 for value in values { if value == target { return true } }
 return false
}
fn EqualBox[T: Equatable](a: Box[T], b: Box[T]) -> bool = a == b
fn Forward[T: Equatable](values: [T], target: T) -> bool = Contains(values, target)
effect fn main() {
 print(Contains([State.Waiting, State.Paid(7)], State.Paid(7)))
 print(Forward([[1, 2], [3]], [2, 1]))
 let compare = EqualBox::[int]
 print(compare(Box::[int]{value: 4}, Box::[int]{value: 4}))
}
'''


class NativeConstraintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = native_service()
        cls.addClassCleanup(cls.service.close)

    def request(self, operation, **args):
        response = self.service.request(dict(version=1, operation=operation, **args))
        self.assertEqual(response['status'], 'ok', response)
        return response['result']

    def test_plain_records_do_not_consume_generic_instantiation_budget(self):
        declarations = [f'record R{i} {{ value:int }}' for i in range(260)]
        body = ';'.join(f'let r{i}=R{i}{{value:{i}}}' for i in range(260))
        source = '\n'.join(declarations) + '\neffect fn main(){' + body + ';print(r259.value)}'
        self.assertEqual(VM(self.request('compile', files={'main.pxl': source})['bytecode']).run(), [259])
        generic = 'record Box[T]{value:T}\n' + '\n'.join(declarations)
        body = ';'.join(f'let b{i}=Box{{value:R{i}{{value:{i}}}}}' for i in range(257))
        response = self.service.request(dict(version=1, operation='compile',
            files={'main.pxl': generic + '\neffect fn main(){' + body + '}'}))
        self.assertEqual(response['status'], 'diagnostics', response)
        self.assertIn('Generic nominal schema limit 256', str(response))

    def test_inference_forwarding_specialization_and_png(self):
        files = {'main.pxl': SOURCE}
        expected = [True, False, True]
        self.assertEqual(VM(self.request('compile', files=files)['bytecode']).run(), expected)
        image = self.request('export-png', files=files)['image']
        self.assertEqual(VM(self.request('compile-png', image=image)['bytecode']).run(), expected)
        recovered = self.request('recover-png', image=image)
        self.assertTrue(any(': Equatable' in text for text in recovered['files'].values()))
        self.assertEqual(VM(self.request('compile', **recovered)['bytecode']).run(), expected)

    def test_constraints_are_enforced_even_if_body_does_not_compare(self):
        declaration = 'fn Accept[T: Equatable](value: T) -> T = value\n'
        sources = [
            declaration + 'effect fn main() { let f = Accept::[fn(int) -> int] }',
            declaration + 'effect fn main() { Accept(fn(x: int) = x) }',
            declaration + 'fn Forward[T](value: T) -> T = Accept(value)\neffect fn main() {}',
            'record Box[T: Equatable] { value: T }\nfn Unused(value: Box[fn(int) -> int]) {}\neffect fn main() {}',
            'fn Bad[T: Unknown](value: T) -> T = value\neffect fn main() {}',
        ]
        for source in sources:
            with self.subTest(source=source):
                response = self.service.request(dict(version=1, operation='compile', files={'main.pxl': source}))
                self.assertNotEqual(response['status'], 'ok', response)
                self.assertRegex(str(response), 'generic capability')

    def test_unused_forward_annotation_checks_complete_nominal_schema(self):
        for fields, valid in [('value:int', True), ('callback:fn(int)->int', False)]:
            for reverse in (False, True):
                with self.subTest(fields=fields, reverse=reverse):
                    declarations = [
                        'fn Unused(value:Box[Later]) {}',
                        'record Box[T:Equatable] { value:T }',
                        'record Later {' + fields + '}']
                    if reverse:
                        declarations.reverse()
                    source = '\n'.join(declarations) + '\neffect fn main(){}'
                    response = self.service.request(dict(version=1, operation='compile',
                        files={'main.pxl': source}, incremental=False))
                    if valid:
                        self.assertEqual(response['status'], 'ok', response)
                    else:
                        self.assertEqual(response['status'], 'diagnostics', response)
                        self.assertIn('generic capability', str(response))

    def test_imported_constraint_change_invalidates_cached_caller(self):
        files = {
            'main.pxl': 'import "library.pxl" as library\neffect fn main() { library.Accept(fn(x: int) = x) }',
            'library.pxl': 'export fn Accept[T](value: T) -> T = value',
        }
        self.request('compile', files=files)
        files['library.pxl'] = 'export fn Accept[T: Equatable](value: T) -> T = value'
        response = self.service.request(dict(version=1, operation='compile', files=files))
        self.assertNotEqual(response['status'], 'ok', response)
        self.assertIn('generic capability', str(response))

    def test_collection_membership_and_stable_unique_isolate_results(self):
        source = '''import "std/collections.pxl" as collections
record Item { value: int }
effect fn main() {
 let values = [Item{value: 2}, Item{value: 1}, Item{value: 2}]
 print(collections.Contains(values, Item{value: 1}))
 var unique = collections.Unique(values)
 print(unique)
 unique[0].value = 9
 print(values[0].value)
 print(collections.Unique([3, 1, 3, 2, 1]))
 print(collections.Sorted([3, 1, 3, 2]))
 print(collections.Sorted(["z", "a", "b"]))
 print(collections.Sum([1, 2, 3], 0))
 print(collections.Sum([1.5, 2.5], 0.0))
 print(collections.Sum([], 4.0))
}'''
        library = Path(__file__).resolve().parents[1] / 'pixellang/stdlib/collections.pxl'
        files = {'main.pxl': source, 'std/collections.pxl': library.read_text(), 'std/error.pxl': library.with_name('error.pxl').read_text()}
        expected = [True, [{'value': 2}, {'value': 1}], 2, [3, 1, 2], [1, 2, 3, 3], ['a', 'b', 'z'], 6, 4.0, 4.0]
        self.assertEqual(VM(self.request('compile', files=files)['bytecode']).run(), expected)
        image = self.request('export-png', files=files)['image']
        self.assertEqual(VM(self.request('compile-png', image=image)['bytecode']).run(), expected)

    def test_ordered_numeric_hierarchy_and_operators_survive_png(self):
        source = '''fn Same[T: Equatable](a: T, b: T) -> bool = a == b
fn Lower[T: Ordered](a: T, b: T) -> T {
 if a < b { return a }
 return b
}
fn Affine[T: Numeric](value: T, scale: T, offset: T) -> T = value * scale + offset
fn NumericOps[T: Numeric](a: T, b: T) -> [T] = [a + b, a - b, a * b, a / b, a % b, -a]
fn Forward[T: Numeric](a: T, b: T) -> bool = Same(Lower(a, b), a)
effect fn main() {
 print(Lower("z", "a"))
 print(Lower(3, 2))
 print(Affine(2.0, 3.0, 1.0))
 print(NumericOps(7, 2))
 print(NumericOps(7.0, 2.0))
 print(Forward(2, 3))
}'''
        files = {'main.pxl': source}
        expected = ['a', 2, 7.0, [9, 5, 14, 3, 1, -7], [9.0, 5.0, 14.0, 3.5, 1.0, -7.0], True]
        self.assertEqual(VM(self.request('compile', files=files)['bytecode']).run(), expected)
        image = self.request('export-png', files=files)['image']
        recovered = self.request('recover-png', image=image)
        self.assertTrue(any(': Ordered' in text and ': Numeric' in text for text in recovered['files'].values()))
        self.assertEqual(VM(self.request('compile', **recovered)['bytecode']).run(), expected)

    def test_ordering_and_numeric_constraints_do_not_invent_capabilities(self):
        sources = [
            'fn F[T: Numeric](a: T) -> T = a\neffect fn main() { F("text") }',
            'fn F[T: Ordered](a: T) -> T = a\neffect fn main() { F(true) }',
            'fn F[T: Ordered](a: T) -> T = a\neffect fn main() { F([1]) }',
            'fn N[T: Numeric](a: T) -> T = a\nfn F[T: Ordered](a: T) -> T = N(a)\neffect fn main() {}',
            'fn F[T: Ordered](a: T, b: T) -> T = a + b\neffect fn main() {}',
            'fn F[T: Equatable](a: T, b: T) -> bool = a < b\neffect fn main() {}',
            'fn F[T: Numeric](a: T, b: T) -> T = a + b\neffect fn main() { F(1, 2.0) }',
        ]
        for source in sources:
            with self.subTest(source=source):
                response = self.service.request(dict(version=1, operation='compile', files={'main.pxl': source}))
                self.assertNotEqual(response['status'], 'ok', response)
                self.assertNotIn("'phase': 'parse'", str(response))

    def test_hashable_implies_equality_and_preserves_nominal_keys_in_png(self):
        source = '''frame World = meters
enum Key { Label(text: string), Code(value: int), Position(point: Point3[World]) }
fn SameKey[T: Hashable](a: T, b: T) -> bool = a == b
fn NumberKey[T: Numeric](a: T, b: T) -> bool = SameKey(a, b)
fn OrderedKey[T: Ordered](a: T, b: T) -> bool = SameKey(a, b)
effect fn main() {
 print(SameKey(Key.Label("a"), Key.Label("a")))
 print(SameKey(Key.Code(1), Key.Code(2)))
 print(SameKey(true, true))
 print(NumberKey(0.0, -0.0))
 print(OrderedKey("a", "b"))
 print(SameKey(Key.Position(Point3::[World]{x: 1.0, y: 2.0, z: 3.0}), Key.Position(Point3::[World]{x: 1.0, y: 2.0, z: 3.0})))
}'''
        files = {'main.pxl': source}
        expected = [True, False, True, True, False, True]
        self.assertEqual(VM(self.request('compile', files=files)['bytecode']).run(), expected)
        image = self.request('export-png', files=files)['image']
        recovered = self.request('recover-png', image=image)
        self.assertTrue(any(': Hashable' in text for text in recovered['files'].values()))
        self.assertEqual(VM(self.request('compile', **recovered)['bytecode']).run(), expected)

    def test_hashable_rejects_non_hashable_keys_and_inspects_inactive_variants(self):
        prefix = 'fn Key[T: Hashable](value: T) -> T = value\n'
        sources = [
            prefix + 'effect fn main() { Key([1]) }',
            prefix + 'effect fn main() { Key(map[string, int]{"a": 1}) }',
            prefix + 'record R { value: int }\neffect fn main() { Key(R{value: 1}) }',
            prefix + 'enum E { Empty, Mutable(value: [int]) }\neffect fn main() { Key(E.Empty) }',
            prefix + 'fn Forward[T: Equatable](value: T) -> T = Key(value)\neffect fn main() {}',
        ]
        for source in sources:
            with self.subTest(source=source):
                response = self.service.request(dict(version=1, operation='compile', files={'main.pxl': source}))
                self.assertNotEqual(response['status'], 'ok', response)
                self.assertIn('generic capability', str(response))


if __name__ == '__main__':
    unittest.main()
