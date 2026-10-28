"""Receiver operations on value collections, including absence and delivery."""
from tests.native_compiler import native_service
import unittest
from pixellang.vm import VM


class NativeCollectionMethodTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = native_service()
        cls.addClassCleanup(cls.compiler.close)

    def compile(self, source):
        return self.compiler.request(dict(version=1, operation='compile', files={'main.pxl': source}))

    def test_optional_lookup_has_one_public_entry_and_name_is_available(self):
        response = self.compile('effect fn main(){print(lookup(map[string,int]{}, "x"))}')
        self.assertEqual(response['status'], 'diagnostics', response)
        source = '''fn lookup(value: int) -> int = value + 1
effect fn main() {
 let table = map[int, [int]]{7: [1]}
 var selected = match table.get(7) { .Some(value) => value, .None => [] }
 selected.append(lookup(1))
 print(table[7]); print(selected); print(table.get(9))
}'''
        expected = [[1], [1, 2], {'variant': 'None', 'fields': {}}]
        response = self.compile(source)
        self.assertEqual(response['status'], 'ok', response)
        self.assertEqual(VM(response['result']['bytecode']).run(), expected)
        exported = self.compiler.request(dict(version=1, operation='export-png', files={'main.pxl': source}))
        self.assertEqual(exported['status'], 'ok', exported)
        image = exported['result']['image']
        compiled = self.compiler.request(dict(version=1, operation='compile-png', image=image))
        self.assertEqual(compiled['status'], 'ok', compiled)
        self.assertEqual(VM(compiled['result']['bytecode']).run(), expected)
        recovered = self.compiler.request(dict(version=1, operation='recover-png', image=image))
        self.assertEqual(recovered['status'], 'ok', recovered)
        rebuilt = self.compiler.request(dict(version=1, operation='compile', **recovered['result']))
        self.assertEqual(rebuilt['status'], 'ok', rebuilt)
        self.assertEqual(VM(rebuilt['result']['bytecode']).run(), expected)

    def test_map_queries_preserve_receiver_snapshot_and_png_delivery(self):
        source = """fn Add(table: inout map[int,string])->int { table[2]="two"; return 2 }
fn Query[K:Hashable,V](table:map[K,V], key:K)->bool=table.has(key)
effect fn main() {
 var table=map[int,string]{1:"one"}
 let before=table.keys()
 print(table.has(Add(&table))); print(Query(table,2)); print(before); print(table.keys())
 let copy=table
 print(copy.get(2)); print((map[string,int]{"x":1}).has("x"))
}"""
        expected = [False, True, [1], [1, 2], {'variant':'Some','fields':{'value':'two'}}, True]
        for operation in ('compile', 'export-png'):
            response = self.compiler.request(dict(version=1, operation=operation, files={'main.pxl': source}))
            self.assertEqual(response['status'], 'ok', response)
            if operation == 'export-png':
                recovered = self.compiler.request(dict(version=1, operation='recover-png', image=response['result']['image']))
                self.assertEqual(recovered['status'], 'ok', recovered)
                response = self.compiler.request(dict(version=1, operation='compile', **recovered['result']))
                self.assertEqual(response['status'], 'ok', response)
            self.assertEqual(VM(response['result']['bytecode']).run(), expected)

    def test_map_queries_reject_old_globals_wrong_keys_and_arity(self):
        for expression in ('has(table, 1)', 'keys(table)', 'table.has("1")',
                           'table.has()', 'table.keys(1)', 'table.contains(1)'):
            with self.subTest(expression=expression):
                response = self.compile('effect fn main(){let table=map[int,string]{};print('+expression+')}')
                self.assertEqual(response['status'], 'diagnostics', response)
        response = self.compile('fn has(value:int)->int=value+1\nfn keys(value:int)->int=value*2\neffect fn main(){print(has(keys(3)))}')
        self.assertEqual(response['status'], 'ok', response)
        self.assertEqual(VM(response['result']['bytecode']).run(), [7])

    def test_collection_completion_uses_checker_method_signatures(self):
        from pixellang.native_editor import NativeSymbolIndex
        def analyze(files):
            response = self.compiler.request(dict(version=1, operation='editor-index', files=files))
            self.assertEqual(response['status'], 'ok', response)
            return NativeSymbolIndex(files, 'main.pxl', response['result']['index'])
        for declaration, expected in (
            ('let value=map[int,string]{}', {'get','has','keys','remove'}),
            ('var value:[int]=[]', {'append','extend','pop'}),
        ):
            with self.subTest(declaration=declaration):
                prefix='fn main(){'+declaration+';value.'
                files={'main.pxl':prefix+'}'}
                items=analyze(files).complete('main.pxl',len(prefix),analyze=analyze)
                self.assertEqual({item['label'] for item in items if item['kind']=='Method'},expected)
                self.assertTrue(all('fn' in item['detail'] for item in items if item['kind']=='Method'))

    def test_array_methods_preserve_aliases_and_return_optional_pop(self):
        source = '''effect fn main() {
 let original = [1]
 var edited = original
 edited.append(2)
 edited.extend([3, 4])
 print(original); print(edited)
 print(edited.pop()); print(edited)
 var empty: [int] = []
 print(empty.pop()); print(empty)
}'''
        response = self.compile(source)
        self.assertEqual(response['status'], 'ok', response)
        self.assertEqual(VM(response['result']['bytecode']).run(),
            [[1], [1, 2, 3, 4], {'variant': 'Some', 'fields': {'value': 4}},
             [1, 2, 3], {'variant': 'None', 'fields': {}}, []])

    def test_map_get_remove_and_missing_values(self):
        response = self.compile('''effect fn main() {
 let original = map[string, int]{"a": 1}
 var edited = original
 print(original.get("a")); print(original.get("missing"))
 print(edited.remove("a")); print(edited.remove("a"))
 print(original); print(edited)
}''')
        self.assertEqual(response['status'], 'ok', response)
        some, none = {'variant': 'Some', 'fields': {'value': 1}}, {'variant': 'None', 'fields': {}}
        self.assertEqual(VM(response['result']['bytecode']).run(), [some, none, some, none, {'a': 1}, {}])

    def test_generic_methods_and_self_extension(self):
        response = self.compile('''fn Added[T](values: [T], item: T) -> [T] {
 var result = values
 result.append(item)
 return result
}
effect fn main() {
 let original = [1]
 var doubled = Added(original, 2)
 doubled.extend(doubled)
 print(original); print(doubled); print(Added(["a"], "b"))
}''')
        self.assertEqual(response['status'], 'ok', response)
        self.assertEqual(VM(response['result']['bytecode']).run(), [[1], [1, 2, 1, 2], ['a', 'b']])

    def test_nested_receiver_evaluation_order_and_isolation(self):
        response = self.compile('''record Group { values: [int] }
effect fn index() -> int { print("index"); return 0 }
effect fn item() -> int { print("item"); return 2 }
effect fn main() {
 let original = [Group{values: [1]}]
 var edited = original
 edited[index()].values.append(item())
 print(original[0].values); print(edited[0].values)
}''')
        self.assertEqual(response['status'], 'ok', response)
        self.assertEqual(VM(response['result']['bytecode']).run(), ['index', 'item', [1], [1, 2]])

    def test_rejects_readonly_receivers_wrong_arguments_and_unknown_methods(self):
        for body in ['let x = [1]; x.append(2)',
                     'var x = [1]; x.append("bad")',
                     'var x = [1]; x.pop(2)',
                     'var x = [1]; x.remove(0)',
                     'var x = map[string, int]{}; x.remove(2)',
                     'var x = [1]; x.extend([true])']:
            with self.subTest(body=body):
                self.assertNotEqual(self.compile('effect fn main() { ' + body + ' }')['status'], 'ok')

    def test_overlapping_value_writes_are_rejected_before_evaluating_addresses(self):
        cases = [
            'var x = [1]; x.append(match x.pop() { Option[int].Some(v) => v, Option[int].None => 0 })',
            'var x = [1]; x[0] = match x.pop() { Option[int].Some(v) => v, Option[int].None => 0 }',
            'var x = [[1], [2]]; x[0].append(match x[1].pop() { Option[int].Some(v) => v, Option[int].None => 0 })',
            'var x = [0]; x[match x.pop() { Option[int].Some(v) => v, Option[int].None => 0 }] = 7',
            'var x = [[0]]; x[match x[0].pop() { Option[int].Some(v) => v, Option[int].None => 0 }].append(7)',
        ]
        for body in cases:
            with self.subTest(body=body):
                response = self.compile('effect fn main() { ' + body + ' }')
                self.assertNotEqual(response['status'], 'ok', response)
                self.assertIn('Overlapping value writes', str(response))

    def test_distinct_roots_and_sequential_writes_remain_legal(self):
        response = self.compile('''effect fn main() {
 let original = [1]
 var x = original
 var y = original
 x.append(match y.pop() { Option[int].Some(v) => v, Option[int].None => 0 })
 x[0] = x[1] + 1
 x.append(3)
 print(original); print(x); print(y)
}''')
        self.assertEqual(response['status'], 'ok', response)
        self.assertEqual(VM(response['result']['bytecode']).run(), [[1], [2, 1, 3], []])

    def test_png_preserves_receiver_writeback_and_optional_results(self):
        source = '''record Group { values: [int] }
effect fn main() {
 let original = Group{values: [1]}
 var edited = original
 edited.values.append(2)
 print(edited.values.pop()); print(original.values); print(edited.values)
}'''
        exported = self.compiler.request(dict(version=1, operation='export-png', files={'main.pxl': source}))
        self.assertEqual(exported['status'], 'ok', exported)
        image = exported['result']['image']
        compiled = self.compiler.request(dict(version=1, operation='compile-png', image=image))
        self.assertEqual(compiled['status'], 'ok', compiled)
        expected = [{'variant': 'Some', 'fields': {'value': 2}}, [1], [1]]
        self.assertEqual(VM(compiled['result']['bytecode']).run(), expected)
        recovered = self.compiler.request(dict(version=1, operation='recover-png', image=image))
        self.assertEqual(recovered['status'], 'ok', recovered)
        rebuilt = self.compiler.request(dict(version=1, operation='compile', **recovered['result']))
        self.assertEqual(rebuilt['status'], 'ok', rebuilt)
        self.assertEqual(VM(rebuilt['result']['bytecode']).run(), expected)


if __name__ == '__main__':
    unittest.main()
