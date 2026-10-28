"""Generic value reads retain their declared type without snapshot projections."""
from tests.native_compiler import native_service
import unittest
from pixellang.vm import VM


class NativeGenericValueTests(unittest.TestCase):
    def test_bulk_extension_preserves_values_through_png_and_recovery(self):
        source = '''fn join[T](target: inout [T], source: [T]) { target.extend(source) }
effect fn main() {
 var values: [[int]]=[]
 var i=0
 while i<33 { values.append([i]); i+=1 }
 let original=values
 join(&values,original)
 values[33][0]=999
 print(original[0]); print(values[33]); print(values[65])
 print(len(original)); print(len(values))
}
'''
        expected = [[0], [999], [32], 33, 66]
        self.assertEqual(self.execute(source), expected)
        exported = self.request(source, 'export-png')
        self.assertEqual(exported['status'], 'ok', exported)
        image = exported['result']['image']
        delivered = self.compiler.request(dict(version=1, operation='compile-png', image=image))
        self.assertEqual(delivered['status'], 'ok', delivered)
        self.assertEqual(VM(delivered['result']['bytecode']).run(), expected)
        recovered = self.compiler.request(dict(version=1, operation='recover-png', image=image))
        self.assertEqual(recovered['status'], 'ok', recovered)
        rebuilt = self.compiler.request(dict(version=1, operation='compile', **recovered['result']))
        self.assertEqual(rebuilt['status'], 'ok', rebuilt)
        self.assertEqual(VM(rebuilt['result']['bytecode']).run(), expected)

    @classmethod
    def setUpClass(cls):
        cls.compiler = native_service()
        cls.addClassCleanup(cls.compiler.close)

    def request(self, source, operation='compile'):
        return self.compiler.request(dict(version=1, operation=operation, files={'main.pxl': source}))

    def execute(self, source):
        response = self.request(source)
        self.assertEqual(response['status'], 'ok', response)
        return VM(response['result']['bytecode']).run()

    def test_array_map_record_and_enum_reads_preserve_type_parameters(self):
        source = '''record Box[T] { value: T }
enum Choice[T] { Item(value: T) }
fn first[T](values: [T]) -> T = values[0]
fn field[T](box: Box[T]) -> T = box.value
fn payload[T](choice: Choice[T]) -> T {
 match choice { Choice.Item(value) => { return value } }
}
fn present[T](value: T) -> Option[T] = Option.Some(value)
fn singleton[T](value: T) -> [T] = [value]
fn table[T](value: T) -> map[string,T] = map[string,T]{"key":value}
fn lookupValue[T](values: map[string,T], fallback: T) -> T {
 return match values.get("key") { Option.Some(value) => value, Option.None => fallback }
}
effect fn main() {
 let value=Box::[[int]]{value:[1,2]}
 print(first(singleton(value)).value)
 print(field(value))
 print(payload(Choice::[Box[[int]]].Item(value)).value)
 print(lookupValue(table(value),value).value)
 match present(value) { Option.Some(found) => {print(found.value)}, Option.None => {} }
}
'''
        self.assertEqual(self.execute(source), [[1, 2]] * 5)

    def test_generic_local_updates_preserve_nested_input_values(self):
        self.assertEqual(self.execute('''record Box[T] { items: [T] }
fn duplicate[T](value: T) -> [T] {
 var values: [T]=[]
 values.append(value); values.append(value)
 return values
}
fn replace[T](before: Box[T], value: T) -> Box[T] {
 var after=before
 after.items[0]=value
 return after
}
effect fn main() {
 let before=Box::[[int]]{items:duplicate([1])}
 var after=replace(before,[2])
 after.items[1][0]=3
 print(before.items); print(after.items)
}
'''), [[[1], [1]], [[2], [3]]])

    def test_generic_callable_values_do_not_require_data_snapshots(self):
        self.assertEqual(self.execute('''fn first[T](values: [T]) -> T = values[0]
fn singleton[T](value: T) -> [T] = [value]
fn increment(value: int) -> int = value+1
effect fn main() { let callback=first(singleton(increment)); print(callback(4)) }
'''), [5])

    def test_generic_parameter_remains_readonly(self):
        response = self.request('''fn replace[T](values: [T], value: T) { values[0]=value }
fn main() {}
''', 'check')
        self.assertNotEqual(response['status'], 'ok')

    def test_generic_values_survive_png_delivery_and_recovery(self):
        source = '''fn singleton[T](value: T) -> [T] = [value]
effect fn main() { print(singleton([1,2])) }
'''
        exported = self.request(source, 'export-png')
        self.assertEqual(exported['status'], 'ok', exported)
        image = exported['result']['image']
        delivered = self.compiler.request(dict(version=1, operation='compile-png', image=image))
        self.assertEqual(delivered['status'], 'ok', delivered)
        self.assertEqual(VM(delivered['result']['bytecode']).run(), [[[1, 2]]])
        recovered = self.compiler.request(dict(version=1, operation='recover-png', image=image))
        self.assertEqual(recovered['status'], 'ok', recovered)
        rebuilt = self.compiler.request(dict(version=1, operation='compile', **recovered['result']))
        self.assertEqual(rebuilt['status'], 'ok', rebuilt)
        self.assertEqual(VM(rebuilt['result']['bytecode']).run(), [[[1, 2]]])
