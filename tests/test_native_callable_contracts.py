"""Native callable contracts across checking, execution and semantic PNGs."""
from tests.native_compiler import native_service
import unittest
from pathlib import Path
import json
import tempfile

from pixellang.fileaccess import FileAccess
from pixellang.vm import VM


SOURCE = '''fn Twice(n: int) -> int = n * 2
fn Apply[T, U](value: T, callback: fn(T) -> U) -> U = callback(value)
fn Pipeline(value: int, callback: fn(int) -> int) -> int = Apply(value, callback)
fn Choose() -> fn(int) -> int = Twice
effect fn main() {
 print(Pipeline(21, Choose()))
 var callback: fn(int) -> int = Twice
 let invoke = fn(n: int) -> int = callback(n)
 callback = fn(n: int) -> int = n + 1
 print(invoke(6))
}
'''


class NativeCallableContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = native_service()
        cls.addClassCleanup(cls.service.close)

    def request(self, operation, **args):
        response = self.service.request(dict(version=1, operation=operation, **args))
        self.assertEqual(response['status'], 'ok', response)
        return response['result']

    def test_generic_callback_and_value_capture(self):
        code = self.request('compile', files={'main.pxl': SOURCE})['bytecode']
        self.assertEqual(VM(code).run(), [42, 12])
        contracts = [typ for fn in code['functions'].values() for typ in fn['params']]
        self.assertIn('fn(int)->(int)', contracts)
        captures = [ins['arg'] for fn in code['functions'].values()
                    for ins in fn['instructions'] if ins['op'] == 'CAPTURE_VALUE']
        self.assertTrue(any(arg['type'] == 'fn(int)->(int)' for arg in captures))

    def test_pixel_delivery_preserves_contract(self):
        image = self.request('export-png', files={'main.pxl': SOURCE})['image']
        self.assertEqual(VM(self.request('compile-png', image=image)['bytecode']).run(), [42, 12])
        recovered = self.request('recover-png', image=image)
        self.assertTrue(any('fn(' in text for text in recovered['files'].values()))
        self.assertEqual(VM(self.request('compile', **recovered)['bytecode']).run(), [42, 12])

    def test_callback_results_preserve_readonly_input(self):
        source = '''fn Identity(values: [int]) -> [int] = values
fn Modify(values: [int], callback: fn([int]) -> [int]) -> [int] {
 var fresh = callback(values)
 fresh.append(9)
 return fresh
}
effect fn main() {
 let values = [1]
 print(Modify(values, Identity))
 print(values)
}'''
        self.assertEqual(VM(self.request('compile', files={'main.pxl': source})['bytecode']).run(), [[1, 9], [1]])

    def test_unknown_and_effectful_callbacks_are_rejected(self):
        for source in [
            'effect fn Effect(n: int) -> int { print(n); return n }\nfn Apply(n: int, f: fn(int) -> int) -> int = f(n)\neffect fn main() { print(Apply(1, Effect)) }',
            'fn Apply(n: int, f: effect fn(int) -> int) -> int = f(n)\nfn main() {}',
            'fn Bad(n: int) -> int { print(n); return n }\nfn main() {}',
            'fn Good(n: int) -> int = n\nfn Take(fs: [effect fn(int) -> int]) {}\nfn main() { let fs = [Good]; Take(fs) }',
        ]:
            with self.subTest(source=source):
                response = self.service.request(dict(version=1, operation='compile', files={'main.pxl': source}))
                self.assertNotEqual(response['status'], 'ok', response)

    def test_callback_in_record_field(self):
        source = '''record Strategy { call: fn(int) -> int }
fn Twice(n: int) -> int = n * 2
fn Apply(strategy: Strategy, n: int) -> int = strategy.call(n)
effect fn main() { print(Apply(Strategy{call: Twice}, 5)) }'''
        self.assertEqual(VM(self.request('compile', files={'main.pxl': source})['bytecode']).run(), [10])

    def test_higher_order_variance_and_imported_contract(self):
        files = {'library.pxl': '''export fn Twice(n: int) -> int = n * 2
export effect fn InvokeAny(callback: effect fn(int) -> int) -> int = callback(3)
export fn Choose() -> fn(int) -> int = Twice''',
                 'main.pxl': '''import "library.pxl" as library
effect fn main() {
 let invoke: effect fn(fn(int) -> int) -> int = library.InvokeAny
 let choose: fn() -> effect fn(int) -> int = library.Choose
 print(invoke(library.Twice))
 print(choose()(4))
}'''}
        self.assertEqual(VM(self.request('compile', files=files)['bytecode']).run(), [6, 8])

    def test_contravariant_narrowing_is_rejected(self):
        source = '''fn PureOnly(callback: fn(int) -> int) -> int = callback(3)
effect fn main() { let unsafe: effect fn(effect fn(int) -> int) -> int = PureOnly }'''
        response = self.service.request(dict(version=1, operation='compile', files={'main.pxl': source}))
        self.assertNotEqual(response['status'], 'ok', response)

    def test_function_values_in_local_array(self):
        source = '''fn Twice(n: int) -> int = n * 2
fn Increment(n: int) -> int = n + 1
effect fn main() {
 var callbacks: [fn(int) -> int] = [Twice]
 callbacks.append(Twice)
 callbacks[0] = Increment
 print(callbacks[0](3))
 print(callbacks[1](3))
}'''
        self.assertEqual(VM(self.request('compile', files={'main.pxl': source})['bytecode']).run(), [4, 6])

    def test_anonymous_value_captures_survive_png(self):
        source = '''effect fn main() {
 var offset = 2
 var values = [3]
 let alias = values
 let callback = fn(n: int) -> int = n + offset + len(values) + len(alias)
 offset = 10
 values.append(4)
 print(callback(1))
 print(offset)
 print(len(values))
}'''
        files = {'main.pxl': source}
        expected = [5, 10, 2]
        self.assertEqual(VM(self.request('compile', files=files)['bytecode']).run(), expected)
        image = self.request('export-png', files=files)['image']
        self.assertEqual(VM(self.request('compile-png', image=image)['bytecode']).run(), expected)
        recovered = self.request('recover-png', image=image)
        self.assertTrue(any('fn(' in text for text in recovered['files'].values()))
        self.assertEqual(VM(self.request('compile', **recovered)['bytecode']).run(), expected)

    def test_standard_collection_callbacks_inside_pure_function(self):
        source = '''import "std/collections.pxl" as collections
fn Compute(values: [int], factor: int) -> [int] {
 let selected = collections.Filter(values, fn(x) = x > 1)
 let ordered = collections.SortedBy(selected, fn(a, b) = a > b)
 var result = collections.Map(ordered, fn(x) = x * factor)
 result.append(collections.Fold(ordered, 0, fn(total, item) = total + item))
 let groups = collections.GroupBy(ordered, fn(item) = string(item % 2))
 result.append(len(groups["0"]))
 return result
}
effect fn main() { print(Compute([1, 3, 2], 2)) }'''
        library = Path(__file__).resolve().parents[1] / 'pixellang/stdlib/collections.pxl'
        files = {'main.pxl': source, 'std/collections.pxl': library.read_text(), 'std/error.pxl': library.with_name('error.pxl').read_text()}
        self.assertEqual(VM(self.request('compile', files=files)['bytecode']).run(), [[6, 4, 5, 1]])

    def test_anonymous_effects_and_capture_mutations_are_rejected(self):
        for body in ('print(n); return n', 'values.append(n); return n',
                     'offset += n; return offset',
                     'let mutate = fn() { values.append(n) }; mutate(); return n'):
            source = 'effect fn main() { var offset = 0; let values = [1]; let f = fn(n: int) -> int { ' + body + ' } }'
            with self.subTest(body=body):
                response = self.service.request(dict(version=1, operation='compile', files={'main.pxl': source}))
                self.assertNotEqual(response['status'], 'ok', response)

    def test_computing_a_callback_cannot_mutate_readonly_input(self):
        source = '''fn Identity(n: int) -> int = n
fn Factory(values: [int]) -> fn(int) -> int { values.append(9); return Identity }
fn Apply(values: [int]) -> int = Factory(values)(1)
effect fn main() { print(Apply([1])) }'''
        response = self.service.request(dict(version=1, operation='compile', files={'main.pxl': source}))
        self.assertNotEqual(response['status'], 'ok', response)

    def test_generic_closure_returns_independent_values(self):
        source = '''fn Remember[T](value: T) -> fn() -> T = fn() = value
effect fn main() {
 var original = [2]
 let remember = Remember(original)
 original.append(9)
 var first = remember()
 first.append(8)
 print(first)
 print(remember())
 print(original)
}'''
        self.assertEqual(VM(self.request('compile', files={'main.pxl': source})['bytecode']).run(),
                         [[2, 8], [2], [2, 9]])

    def test_data_processing_examples_with_pure_callbacks(self):
        root = Path(__file__).resolve().parents[1]
        library = {'std/' + path.name: path.read_text()
                   for path in (root / 'pixellang/stdlib').glob('*.pxl')}
        for name in ('callback-analysis', 'ledger', 'log-analysis'):
            with self.subTest(project=name), tempfile.TemporaryDirectory() as directory:
                project = root / 'examples' / name
                files = {path.name: path.read_text() for path in project.glob('*.pxl')}
                files.update(library)
                fixtures = project if name == 'callback-analysis' else project / 'fixtures'
                request = fixtures / ('input.json' if name == 'callback-analysis' else 'config.json')
                expected = json.loads((fixtures / 'expected.json').read_text())
                code = self.request('compile', files=files)['bytecode']
                image = self.request('export-png', files=files)['image']
                recovered = self.request('recover-png', image=image)
                rebuilt = self.request('compile', **recovered)['bytecode']
                for executable in (code, rebuilt):
                    vm = VM(executable, input_text=request.read_text(), max_steps=10_000_000,
                            file_access=FileAccess(fixtures, Path(directory)))
                    self.assertEqual(vm.run(), expected)
                    if name != 'callback-analysis':
                        report = Path(directory) / json.loads(request.read_text())['output']
                        self.assertEqual(json.loads(report.read_text()), expected[0])


if __name__ == '__main__':
    unittest.main()
