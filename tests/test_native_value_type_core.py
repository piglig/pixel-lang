"""The value type core has ordinary names and explicit receiver writes."""
from tests.native_compiler import native_service
import unittest

from pixellang.vm import VM


class NativeValueTypeCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = native_service()
        cls.addClassCleanup(cls.compiler.close)

    def compile(self, source):
        return self.compiler.request(dict(version=1, operation='compile',
                                          files={'main.pxl': source}))

    def test_global_mutators_do_not_bypass_receiver_access(self):
        for expression in ('append(values, 2)', 'extend(values, [2])',
                           'pop(values)', 'delete(table, "a")'):
            with self.subTest(expression=expression):
                result = self.compile('''fn main() {
 var values=[1]
 var table=map[string,int]{"a":1}
 ''' + expression + '\n}')
                self.assertEqual(result['status'], 'diagnostics', result)

    def test_former_type_names_construct_and_match_as_ordinary_values(self):
        result = self.compile('''record Frozen[T] { value:T }
enum mut[T] { Item(value:T) }
fn unwrap[T](item:mut[T])->T {
 match item {mut[T].Item(value)=>{return value}}
}
effect fn main(){
 let before=Frozen::[[int]]{value:[1]}
 var after=before
 after.value.append(2)
 print(before.value)
 print(unwrap(mut::[Frozen[[int]]].Item(after)).value)
}''')
        self.assertEqual(result['status'], 'ok', result)
        self.assertEqual(VM(result['result']['bytecode']).run(), [[1], [1, 2]])
