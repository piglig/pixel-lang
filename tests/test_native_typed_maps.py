"""Native source, pixel delivery and recovery preserve typed map semantics."""
from tests.native_compiler import native_service
import unittest
from pixellang.vm import VM
from pixellang.model import PixelError

SOURCE = '''enum Code { Id(value: int), Empty }
fn Read[K: Hashable, V](table: map[K, V], key: K) -> Option[V] = table.get(key)
effect fn main() {
 var table: map[int, string] = map[int, string]{3: "three", 1: "one"}
 table[3] = "THREE"
 print(table[3])
 print(table.keys())
 for key, value in table { print(key); print(value) }
 for key in table { print(key) }
 print(Read(table, 1))
 print(table.has(9))
 print(table.remove(3))
 table[3] = "again"
 print(table.keys())
 let codes = map[Code, [int]]{Code.Id(7): [1, 2], Code.Empty: []}
 print(codes[Code.Id(7)])
 let recovered = jsonDecode::[map[Code, [int]]](jsonFrom(codes))
 match recovered { .Ok(value) => { print(value == codes); print(value.keys()) }, .Err(error) => { assert(false) } }
 let zero = map[float64, int]{0.0: 5}
 print(zero[-0.0])
 let flags = map[bool, int]{true: 1, false: 2}
 print(flags[false])
 let empty = map[int, string]{}
 print(Read(empty, 1))
}
'''
EXPECTED = ['THREE', [3, 1], 3, 'THREE', 1, 'one', 3, 1,
            {'variant': 'Some', 'fields': {'value': 'one'}}, False, {'variant': 'Some', 'fields': {'value': 'THREE'}}, [1, 3],
            [1, 2], True, [{'variant': 'Id', 'fields': {'value': 7}},
                          {'variant': 'Empty', 'fields': {}}], 5, 2,
            {'variant': 'None', 'fields': {}}]


class NativeTypedMapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = native_service()
        cls.addClassCleanup(cls.service.close)

    def request(self, operation, **arguments):
        response = self.service.request(dict(version=1, operation=operation, **arguments))
        self.assertEqual(response['status'], 'ok', response)
        return response['result']

    def test_typed_keys_generics_iteration_json_and_png_recovery(self):
        files = {'main.pxl': SOURCE}
        code = self.request('compile', files=files)['bytecode']
        self.assertEqual(VM(code).run(), EXPECTED)
        map_ops = [instruction for fn in code['functions'].values() for instruction in fn['instructions']
                   if instruction['op'] == 'MAP']
        self.assertTrue(map_ops)
        self.assertTrue(all(op['arg']['type'].startswith('map<') for op in map_ops))
        image = self.request('export-png', files=files)['image']
        self.assertEqual(VM(self.request('compile-png', image=image)['bytecode']).run(), EXPECTED)
        recovered = self.request('recover-png', image=image)
        self.assertEqual(VM(self.request('compile', **recovered)['bytecode']).run(), EXPECTED)

    def test_spatial_keys_and_state_initializers(self):
        source = """frame World = meters
enum Code { Id(value: int) }
flow Initial() step() -> bool {
 state same: bool = map[Code, int]{Code.Id(1): 2, Code.Id(2): 3} == map[Code, int]{Code.Id(2): 3, Code.Id(1): 2}
 next same = same
 output same
}
flow Table() step() -> map[Code, int] {
 state table: map[Code, int] = map[Code, int]{Code.Id(1): 2}
 next table = table
 output table
}
effect fn main() {
 print(Initial().step())
 let table = Table().step()
 print(table[Code.Id(1)])
 let point = Point3::[World]{x: 1.0, y: 2.0, z: 3.0}
 let positions = map[Point3[World], string]{point: "here"}
 print(positions[Point3::[World]{x: 1.0, y: 2.0, z: 3.0}])
 match jsonDecode::[map[Point3[World], string]](jsonFrom(positions)) { .Ok(value) => { print(value == positions) }, .Err(error) => { assert(false) } }
}
"""
        files = {'main.pxl': source}
        expected = [True, 2, 'here', True]
        self.assertEqual(VM(self.request('compile', files=files)['bytecode']).run(), expected)
        image = self.request('export-png', files=files)['image']
        self.assertEqual(VM(self.request('compile-png', image=image)['bytecode']).run(), expected)
        recovered = self.request('recover-png', image=image)
        self.assertEqual(VM(self.request('compile', **recovered)['bytecode']).run(), expected)

    def test_duplicate_keys_follow_value_equality(self):
        for expression in ['map[int, int]{1: 2, 1: 3}',
                           'map[float64, int]{0.0: 2, -0.0: 3}',
                           'map[Code, int]{Code.Id(1): 2, Code.Id(1): 3}']:
            source = 'enum Code { Id(value: int) }\neffect fn main() { print(' + expression + ') }'
            with self.subTest(expression=expression):
                code = self.request('compile', files={'main.pxl': source})['bytecode']
                with self.assertRaises(PixelError) as error:
                    VM(code).run()
                self.assertEqual(error.exception.code, 'collection.duplicate_key')
        source = """enum Code { Id(value: int) }
flow Bad() step() -> map[Code, int] {
 state table: map[Code, int] = map[Code, int]{Code.Id(1): 2, Code.Id(1): 3}
 next table = table
 output table
}
effect fn main() { print(Bad().step()) }
"""
        response = self.service.request(dict(version=1, operation='compile', files={'main.pxl': source}))
        self.assertNotEqual(response['status'], 'ok', response)
        self.assertIn('Duplicate map key', str(response))

    def test_single_argument_map_syntax_is_rejected(self):
        for source in ['effect fn main() { let table = map[int]{} }',
                       'fn Unused(table: map[int]) {}\neffect fn main() {}']:
            with self.subTest(source=source):
                response = self.service.request(dict(version=1, operation='compile', files={'main.pxl': source}))
                self.assertNotEqual(response['status'], 'ok', response)

    def test_invalid_key_types_and_operations_are_rejected_before_execution(self):
        sources = [
            'effect fn main() { let table = map[[int], string]{} }',
            'record Key { value: int }\neffect fn main() { let table = map[Key, int]{} }',
            'enum Key { Valid, Mutable(value: [int]) }\neffect fn main() { let table = map[Key, int]{} }',
            'fn Bad[K](table: map[K, int]) {}\neffect fn main() {}',
            'effect fn main() { let table = map[int, string]{true: "bad"} }',
            'effect fn main() { let table = map[int, string]{}; print(table["1"]) }',
            'effect fn main() { let table = map[int, string]{}; table[1] = 1 }',
            'effect fn main() { let table = map[int, string]{}; print(table.has("1")) }',
            'effect fn main() { let table = map[int, string]{}; print(table.get(true)) }',
            'effect fn main() { let table = map[int, string]{}; print(table.remove(1.0)) }',
        ]
        for source in sources:
            with self.subTest(source=source):
                response = self.service.request(dict(version=1, operation='compile', files={'main.pxl': source}))
                self.assertNotEqual(response['status'], 'ok', response)


if __name__ == '__main__':
    unittest.main()
