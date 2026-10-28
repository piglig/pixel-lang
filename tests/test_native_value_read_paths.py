"""Collection reads retain T without a second permission-projection type."""
from tests.native_compiler import native_service
import unittest

from pixellang.vm import VM


class NativeValueReadPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = native_service()
        cls.addClassCleanup(cls.compiler.close)

    def test_generic_iteration_slice_repeat_and_lookup_keep_value_types(self):
        source = '''record Box[T] { value: T }
fn first[T](values: [T], fallback: T) -> T {
 for value in values { return value }
 return fallback
}
fn firstMapped[T](values: map[string,T], fallback: T) -> T {
 for key, value in values { return value }
 return fallback
}
fn copy[T](values: [T]) -> [T] = slice(values,0,len(values))
fn repeat[T](values: [T]) -> [T] = arrayRepeat(values,2)
fn get[T](values: map[string,T]) -> Option[T] = values.get("a")
effect fn main() {
 let original=Box::[[int]]{value:[1,2]}
 let values=[original]
 let mapped=map[string,Box[[int]]]{"a":original}
 var selected=first(copy(repeat(values)),original)
 selected.value.append(3)
 print(selected.value)
 print(firstMapped(mapped,original).value)
 match get(mapped) {Option.Some(value)=>{print(value.value)},Option.None=>{print([]:[int])}}
 print(original.value)
}
'''
        response = self.compiler.request(dict(version=1, operation='compile',
                                             files={'main.pxl': source}))
        self.assertEqual(response['status'], 'ok', response)
        self.assertEqual(VM(response['result']['bytecode']).run(),
                         [[1, 2, 3], [1, 2], [1, 2], [1, 2]])
