"""The native compiler must agree on constant, runtime and PNG equality."""
from tests.native_compiler import native_service
import unittest

from pixellang.vm import VM


SOURCE = '''frame World = meters
enum Status { Ready, Done(value: int) }
record Item { value: int, status: Status }
flow Initial() step() -> bool {
 state same: bool = map[string, int]{"a": 1, "b": 2} == map[string, int]{"b": 2, "a": 1}
 state item: bool = Item{value: 3, status: Status.Done(4)} == Item{status: Status.Done(4), value: 3}
 next same = same
 next item = item
 let combine = Both(same, item)
 output combine
}
fn Both(a: bool, b: bool) -> bool = a && b
effect fn main() {
 print(Initial().step())
 print(map[string, int]{"a": 1, "b": 2} == map[string, int]{"b": 2, "a": 1})
 print(Item{value: 3, status: Status.Done(4)} == Item{status: Status.Done(4), value: 3})
 print(Status.Ready != Status.Done(4))
 print(Status.Done(4) == Status.Done(5))
 print([1, 2] == [2, 1])
 print([Status.Done(4)] == [Status.Done(4)])
 print([Point3::[World]{x: 1.0, y: 2.0, z: 3.0}] == [Point3::[World]{x: 1.0, y: 2.0, z: 3.0}])
}
'''


class NativeEqualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = native_service()
        cls.addClassCleanup(cls.service.close)

    def request(self, operation, **args):
        response = self.service.request(dict(version=1, operation=operation, **args))
        self.assertEqual(response['status'], 'ok', response)
        return response['result']

    def test_constant_runtime_and_png_agree(self):
        files = {'main.pxl': SOURCE}
        expected = [True, True, True, True, False, False, True, True]
        code = self.request('compile', files=files)['bytecode']
        self.assertEqual(VM(code).run(), expected)
        image = self.request('export-png', files=files)['image']
        self.assertEqual(VM(self.request('compile-png', image=image)['bytecode']).run(), expected)
        recovered = self.request('recover-png', image=image)
        self.assertEqual(VM(self.request('compile', **recovered)['bytecode']).run(), expected)

    def test_noncomparable_types_and_mismatched_types_are_rejected(self):
        sources = [
            'effect fn main() { print(1 == 1.0) }',
            'effect fn main() { let a: [fn(int) -> int] = []; print(a == a) }',
            'enum E { Empty, Callback(call: fn(int) -> int) }\neffect fn main() { print(E.Empty == E.Empty) }',
            'fn Same[T](a: T, b: T) -> bool = a == b\neffect fn main() { print(Same(1, 1)) }',
        ]
        for source in sources:
            with self.subTest(source=source):
                response = self.service.request(dict(version=1, operation='compile', files={'main.pxl': source}))
                self.assertNotEqual(response['status'], 'ok', response)
                self.assertRegex(str(response), 'Equality requires identical operand types|Type does not support value equality')

    def test_empty_function_maps_are_rejected_by_equality_and_generic_constraints(self):
        for source in (
            'effect fn main(){let values:map[string,fn()->int]=map[string,fn()->int]{};print(values==values)}',
            'fn Same[T:Equatable](value:T)->bool=value==value\neffect fn main(){let values:map[string,fn()->int]=map[string,fn()->int]{};print(Same(values))}',
        ):
            with self.subTest(source=source):
                response = self.service.request(dict(version=1, operation='check', files={'main.pxl': source}))
                self.assertNotEqual(response['status'], 'ok', response)
                self.assertRegex(str(response), 'Type does not support value equality|does not satisfy generic capability')


if __name__ == '__main__':
    unittest.main()
