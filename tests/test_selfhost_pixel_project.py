import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from pixellang.bootstrap import read_bytecode_stream
from pixellang.codec import rgba
from pixellang.project import compile_project
from pixellang.vm import VM
from pixellang.model import PixelError
from pixellang.fileaccess import FileAccess
from tests.selfhost_runtime import SELFHOST_HEAP_LIMITS


class PixelProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.compiler = compile_project({p.name: p.read_text() for p in (root / 'selfhost').glob('*.pxl')})[1].bytecode

    def request(self, request, file_access=None):
        with patch('pixellang.text.parse_text', side_effect=AssertionError('host text parse')), patch(
            'pixellang.parser.parse', side_effect=AssertionError('host pixel parse')), patch(
            'pixellang.spatial.analyze', side_effect=AssertionError('host spatial')), patch(
            'pixellang.semantics.Checker.check', side_effect=AssertionError('host check')), patch(
            'pixellang.backend.lower', side_effect=AssertionError('host lower')), patch(
            'pixellang.backend.assemble', side_effect=AssertionError('host assemble')):
            return VM(self.compiler, **SELFHOST_HEAP_LIMITS, input_text=json.dumps(request), file_access=file_access,
                max_steps=100_000_000, max_depth=1024, max_output_chars=64_000_000).run()

    def compile(self, source, operation='pixel-bytecode', modules=None):
        document = dict(source)
        bundled = document.pop('bundle', {'modules': {}})['modules']
        output = self.request(dict(source='fixture.pixel', text='', operation=operation,
            document=document, modules=bundled if modules is None else modules))
        return read_bytecode_stream(output) if operation.endswith('-stream') else json.loads(output[0])

    def test_native_multimodule_program_uses_no_host_compiler(self):
        source = compile_project({
            'main.pxl': 'import "data.pxl" as data\nimport "worker.pxl" as work\nfn main() { let box = data.Box[int]{value: 7}; print(work.read(box)); print(data.identity("ok")) }',
            'data.pxl': 'export record Box[T] { value: T }\nexport fn identity[T](value: T) -> T = value',
            'worker.pxl': 'import "data.pxl" as data\nexport fn read(box: data.Box[int]) -> int = box.value',
        })[0]
        result = self.compile(source)
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(VM(result['bytecode']).run(), [7, 'ok'])
        self.assertEqual(self.compile(source, 'pixel-bytecode-stream'), result)

    def test_pixellang_emit_decode_check_and_execute_round_trip(self):
        files = {'main.pxl': 'import "math.pxl" as math\nfn main() { print(math.fact(6)); var total = 0; for value in [1, 2, 3] { total += value }; print(total) }',
                 'math.pxl': 'export fn fact(n: int) -> int { if n <= 1 { return 1 }; return n * fact(n - 1) }'}
        encoded = self.request(dict(source='main.pxl', text='', operation='pixels', files=files))[0]
        self.assertEqual(encoded['diagnostics'], [])
        result = self.compile(encoded['source'])
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(VM(result['bytecode']).run(), [720, 6])

    def test_inferred_transitive_nominal_type_round_trip(self):
        files = {
            'main.pxl': 'import "api.pxl" as api\nfn main() { let box = api.make(); print(box.value) }',
            'api.pxl': 'import "types.pxl" as types\nexport fn make() -> types.Box[int] = types.Box[int]{value: 7}',
            'types.pxl': 'export record Box[T] { value: T }',
        }
        encoded = self.request(dict(source='main.pxl', text='', operation='pixels', files=files))[0]
        self.assertEqual(encoded['diagnostics'], [])
        result = self.compile(encoded['source'])
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(VM(result['bytecode']).run(), [7])

    def test_missing_modules_and_invalid_ids_do_not_emit_programs(self):
        source = compile_project({'main.pxl': 'import "lib.pxl" as lib\nfn main() { lib.work() }',
                                  'lib.pxl': 'export fn work() {}'})[0]
        library = next(iter(source['bundle']['modules'].values()))
        for modules in ({}, {'01': library}, {'0': library}, {'65536': library}):
            result = self.compile(source, modules=modules)
            self.assertTrue(result['diagnostics'])
            self.assertEqual(result['bytecode']['entry'], [])
            self.assertEqual(result['bytecode']['functions'], {})

    def test_import_cycle_and_library_entry_are_rejected(self):
        source = compile_project({'main.pxl': 'import "lib.pxl" as lib\nfn main() { lib.work() }',
                                  'lib.pxl': 'export fn work() {}'})[0]
        library = json.loads(json.dumps(source['bundle']['modules']['1']))
        for pixel in library['pixels']:
            pixel['position'][1] += 2
        for edge in library['spatial']['links']:
            edge['from'][1] += 2
            edge['to'][1] += 2
        library['pixels'] += [dict(position=[0, 0], rgba=rgba('keyword', 'import')),
                              dict(position=[1, 0], rgba=rgba('id', 0))]
        library['dimensions'][1] += 2
        result = self.compile(source, modules={'1': library})
        self.assertTrue(any('Cyclic' in d['message'] for d in result['diagnostics']))
        self.assertEqual(result['bytecode']['entry'], [])
        library = compile_project({'main.pxl': 'fn main() {}'})[0]
        library.pop('bundle')
        result = self.compile(source, modules={'1': library})
        self.assertTrue(any('entry' in d['message'] for d in result['diagnostics']))
        self.assertEqual(result['bytecode']['entry'], [])

    def test_missing_root_entry_and_module_limit(self):
        source = compile_project({'main.pxl': 'import "lib.pxl" as lib\nfn main() { lib.work() }',
                                  'lib.pxl': 'export fn work() {}'})[0]
        library = source['bundle']['modules']['1']
        result = self.compile(library)
        self.assertTrue(any('entry' in d['message'] for d in result['diagnostics']))
        self.assertEqual(result['bytecode']['functions'], {})
        result = self.compile(source, modules={str(i): library for i in range(1, 129)})
        self.assertTrue(any('128 modules' in d['message'] for d in result['diagnostics']))
        self.assertEqual(result['bytecode']['functions'], {})

    def test_closure_restoration_and_lexical_capture_intervals(self):
        cases = [
            ('fn main() { var count = 0; let increment = fn() { count += 1 }; let read = fn() = count; increment(); increment(); print(read()); let outer = fn() -> fn()->int { return fn() = count }; count = 9; print(outer()()) }', [2, 9]),
            ('fn main() { let callbacks: [fn()->int] = []; for index, value in [10, 20, 30] { append(callbacks, fn() = index + value) }; print(callbacks[0]()); print(callbacks[2]()) }', [10, 32]),
            ('fn main() { var f = fn(x: int) = x + 1; let change = fn() -> int { f = fn(x: int) = x + 100; return 5 }; print(f(change())); print(f(5)) }', [6, 105]),
        ]
        for text, expected in cases:
            result = self.compile(compile_project({'main.pxl': text})[0])
            self.assertEqual(result['diagnostics'], [])
            self.assertEqual(VM(result['bytecode']).run(), expected)

    def test_generic_imported_closures_round_trip(self):
        files = {'main.pxl': 'import "lib.pxl" as lib\nfn main() { let a = lib.make(7); let b = lib.make("x"); print(a()); print(b()) }',
                 'lib.pxl': 'export fn make[T](value: T) -> fn()->T { return fn() = value }'}
        source = self.request(dict(source='main.pxl', text='', operation='pixels', files=files))[0]
        self.assertEqual(source['diagnostics'], [])
        result = self.compile(source['source'])
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(VM(result['bytecode']).run(), [7, 'x'])

    def test_missing_and_reused_lambda_bodies_are_rejected(self):
        source = compile_project({'main.pxl': 'fn main() { let a = fn() = 1; let b = fn() = 2; print(a()); print(b()) }'})[0]
        broken = json.loads(json.dumps(source))
        body_y = min(p['position'][1] for p in broken['pixels'] if p['rgba'] == rgba('keyword', 'lambda_body'))
        broken['pixels'] = [p for p in broken['pixels'] if p['position'][1] < body_y]
        result = self.compile(broken)
        self.assertTrue(any('Missing lambda' in d['message'] for d in result['diagnostics']))
        self.assertEqual(result['bytecode']['entry'], [])
        references = [p for p in source['pixels'] if p['rgba'] == rgba('keyword', 'lambda')]
        identities = [next(p for p in source['pixels'] if p['position'] == [ref['position'][0] + 4, ref['position'][1]]) for ref in references]
        identities[1]['rgba'] = identities[0]['rgba'][:]
        result = self.compile(source)
        self.assertTrue(any('Reused' in d['message'] for d in result['diagnostics']))
        self.assertEqual(result['bytecode']['entry'], [])

    def test_runtime_error_keeps_reversed_physical_pixel_positions(self):
        source = compile_project({'main.pxl': 'fn main() { print(10 / 0) }'})[0]
        rows = {}
        for pixel in source['pixels']:
            rows.setdefault(pixel['position'][1], []).append(pixel)
        links = []
        for row in rows.values():
            row.sort(key=lambda p: p['position'][0])
            total = row[0]['position'][0] + row[-1]['position'][0]
            for pixel in row:
                pixel['position'][0] = total - pixel['position'][0]
            links.extend(dict(kind='next', **{'from': a['position'][:], 'to': b['position'][:]}) for a, b in zip(row, row[1:]))
        source['spatial']['links'] = links
        expected = {tuple(p['position']) for p in source['pixels'] if p['rgba'] in (rgba('int', 10), rgba('int', 0), rgba('op', '/'))}
        result = self.compile(source)
        self.assertEqual(result['diagnostics'], [])
        with self.assertRaises(PixelError) as caught:
            VM(result['bytecode']).run()
        span = caught.exception.to_dict()['span']
        self.assertEqual(span['kind'], 'spatial')
        self.assertEqual(set(map(tuple, span['points'])), expected)
        self.assertEqual(span['min'], [min(x for x, y in expected), min(y for x, y in expected)])
        self.assertEqual(span['max'], [max(x for x, y in expected), max(y for x, y in expected)])

    def test_real_projects_through_full_pixel_frontend(self):
        root = Path(__file__).resolve().parents[1]
        library = {'std/' + p.name: p.read_text() for p in (root / 'pixellang' / 'stdlib').glob('*.pxl')}
        for name in ('callback-analysis', 'ledger', 'log-analysis'):
            with self.subTest(project=name), tempfile.TemporaryDirectory() as directory:
                project = root / 'examples' / name
                files = {p.name: p.read_text() for p in project.glob('*.pxl')}
                files.update(library)
                encoded = self.request(dict(source='main.pxl', text='', operation='pixels', files=files))[0]
                self.assertEqual(encoded['diagnostics'], [])
                result = self.compile(encoded['source'], 'pixel-bytecode-stream')
                self.assertEqual(result['diagnostics'], [])
                fixtures = project if name == 'callback-analysis' else project / 'fixtures'
                request = fixtures / ('input.json' if name == 'callback-analysis' else 'config.json')
                expected = json.loads((fixtures / 'expected.json').read_text())
                vm = VM(result['bytecode'], input_text=request.read_text(), max_steps=10_000_000,
                        file_access=FileAccess(fixtures, Path(directory)))
                self.assertEqual(vm.run(), expected)
                if name != 'callback-analysis':
                    report = Path(directory) / json.loads(request.read_text())['output']
                    self.assertEqual(json.loads(report.read_text()), expected[0])

    def test_complete_native_document_and_optional_fields(self):
        source = compile_project({'main.pxl': 'import "lib.pxl" as lib\nfn main() { print(lib.value()) }',
                                  'lib.pxl': 'export fn value() -> int = 42'})[0]
        output = self.request(dict(source='bundle.pixel', text='', operation='pixel-document-bytecode-stream', native=source))
        result = read_bytecode_stream(output)
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(VM(result['bytecode']).run(), [42])
        source = compile_project({'main.pxl': 'fn main() { print(7) }'})[0]
        for key in ('metadata', 'spatial', 'bundle'):
            source.pop(key)
        result = json.loads(self.request(dict(source='bare.pixel', text='', operation='pixel-document-bytecode', native=source))[0])
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(VM(result['bytecode']).run(), [7])

    def test_invalid_native_document_shapes_return_diagnostics(self):
        base = compile_project({'main.pxl': 'fn main() {}'})[0]
        cases = [[], {'magic': 'PIXELLANG'}]
        for field, value in [('bundle', None), ('bundle', {'version': '0.7'}),
                             ('bundle', {'version': '0.8', 'modules': []}),
                             ('metadata', None), ('spatial', None)]:
            source = dict(base)
            source[field] = value
            cases.append(source)
        for source in cases:
            with self.subTest(source=source):
                result = json.loads(self.request(dict(source='broken.pixel', text='', operation='pixel-document-bytecode', native=source))[0])
                self.assertTrue(result['diagnostics'])
                self.assertEqual(result['diagnostics'][0]['phase'], 'format')
                self.assertEqual(result['bytecode']['entry'], [])
                self.assertEqual(result['bytecode']['functions'], {})

    def test_pixel_file_loading_and_failure_diagnostics(self):
        source = compile_project({'main.pxl': 'fn main() { print("from file") }'})[0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'program.pixel').write_text(json.dumps(source))
            request = dict(source='program.pixel', text='', operation='pixel-file-bytecode-stream', file='program.pixel')
            result = read_bytecode_stream(self.request(request, FileAccess(root)))
            self.assertEqual(result['diagnostics'], [])
            self.assertEqual(VM(result['bytecode']).run(), ['from file'])
            denied = read_bytecode_stream(self.request(request))
            self.assertEqual(denied['diagnostics'][0]['phase'], 'io')
            self.assertEqual(denied['bytecode']['entry'], [])
            (root / 'program.pixel').write_text('{broken')
            invalid = read_bytecode_stream(self.request(request, FileAccess(root)))
            self.assertEqual(invalid['diagnostics'][0]['phase'], 'format')
            self.assertEqual(invalid['bytecode']['functions'], {})

    def test_underscore_variants_survive_semantic_pixel_roundtrip(self):
        files = {'main.pxl': '''enum Plain { _, Other }
enum Choice[T] { _(value: T), Other }
fn main() {
 let p = Plain.Other
 print(match p { Plain._ => 1, Plain.Other => 2 })
 let c = Choice[int].Other
 print(match c { Choice[int]._(n) => n, Choice[int].Other => 7 })
 let value = Choice[int]._(9)
 match value { Choice[int]._(n) => { print(n) }, _ => { print(0) } }
}'''}
        emitted = self.request(dict(source='main.pxl', text='', operation='pixels', files=files))[0]
        self.assertEqual(emitted['diagnostics'], [])
        result = self.compile(emitted['source'])
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(VM(result['bytecode']).run(), [2, 7, 9])

    def raw_map_program(self, body):
        from pixellang.codec import encode_token, new_source
        rows = [[('keyword', 'entry'), ('keyword', 'fn'), ('id', 1), ('punct', '('), ('punct', ')'), ('punct', '->'), ('type', 'unit')]] + body
        pixels = []
        width = 1
        for y, row in enumerate(rows):
            colors = [color for kind, value in row for color in encode_token(kind, value)]
            left = 0 if y == 0 else 2
            width = max(width, left + len(colors))
            pixels.extend(dict(position=[left + x, y * 2], rgba=color) for x, color in enumerate(colors))
        return new_source(width, len(rows) * 2 - 1, pixels)

    def test_raw_map_inference_and_nested_execution(self):
        source = self.raw_map_program([
            [('keyword', 'let'), ('id', 2), ('punct', '='), ('punct', '{'), ('str', 'outer'), ('punct', ':'),
             ('punct', '{'), ('str', 'value'), ('punct', ':'), ('int', 42), ('punct', '}'), ('punct', '}')],
            [('keyword', 'print'), ('id', 2), ('punct', '['), ('str', 'outer'), ('punct', ']'), ('punct', '['), ('str', 'value'), ('punct', ']')],
            [('keyword', 'let'), ('id', 3), ('punct', ':'), ('type', 'map'), ('punct', '('), ('type', 'int'), ('punct', ')'),
             ('punct', '='), ('punct', '{'), ('punct', '}')],
            [('keyword', 'print'), ('builtin', 'len'), ('punct', '('), ('id', 3), ('punct', ')')],
        ])
        result = self.compile(source)
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(VM(result['bytecode']).run(), [42, 0])

    def test_raw_map_inference_errors_are_not_executable(self):
        for entries in (
            [],
            [('int', 1), ('punct', ':'), ('int', 2)],
            [('str', 'a'), ('punct', ':'), ('int', 1), ('punct', ','), ('str', 'b'), ('punct', ':'), ('bool', True)],
        ):
            source = self.raw_map_program([[('keyword', 'let'), ('id', 2), ('punct', '='), ('punct', '{')] + entries + [('punct', '}')]])
            with self.subTest(entries=entries):
                result = self.compile(source)
                self.assertTrue(result['diagnostics'])
                self.assertFalse(result['bytecode']['functions'])

    def test_generic_empty_map_inference_from_later_argument(self):
        from pixellang.codec import encode_token, new_source
        parameter = [('type', 'parameter'), ('punct', '('), ('id', 3), ('punct', ')')]
        map_type = [('type', 'map'), ('punct', '(')] + parameter + [('punct', ')')]
        rows = [
            (0, [('keyword', 'fn'), ('id', 2), ('punct', '['), ('id', 3), ('punct', ']'), ('punct', '('),
                 ('id', 4), ('punct', ':')] + map_type + [('punct', ','), ('id', 5), ('punct', ':')] + parameter +
                 [('punct', ')'), ('punct', '->')] + map_type),
            (2, [('keyword', 'return'), ('id', 4)]),
            (0, [('keyword', 'entry'), ('keyword', 'fn'), ('id', 1), ('punct', '('), ('punct', ')'), ('punct', '->'), ('type', 'unit')]),
            (2, [('keyword', 'let'), ('id', 6), ('punct', '='), ('id', 2), ('punct', '('), ('punct', '{'), ('punct', '}'), ('punct', ','), ('int', 42), ('punct', ')')]),
            (2, [('keyword', 'print'), ('builtin', 'len'), ('punct', '('), ('id', 6), ('punct', ')')]),
        ]
        pixels = []
        width = 1
        for y, (left, row) in enumerate(rows):
            colors = [color for kind, value in row for color in encode_token(kind, value)]
            width = max(width, left + len(colors))
            pixels.extend(dict(position=[left + x, y * 2], rgba=color) for x, color in enumerate(colors))
        result = self.compile(new_source(width, len(rows) * 2 - 1, pixels))
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(VM(result['bytecode']).run(), [0])

    def test_inferred_map_canonical_pixel_reemission(self):
        source = self.raw_map_program([
            [('keyword', 'let'), ('id', 2), ('punct', '='), ('punct', '{'), ('str', 'a'), ('punct', ':'), ('int', 42), ('punct', '}')],
            [('keyword', 'print'), ('id', 2), ('punct', '['), ('str', 'a'), ('punct', ']')],
        ])
        root = Path(__file__).resolve().parents[1]
        files = {p.name: p.read_text() for p in (root / 'selfhost').glob('*.pxl')}
        files['main.pxl'] = '''import "spatial_layout.pxl" as layout
import "pixel_project.pxl" as project
import "bindings.pxl" as bindings
import "types.pxl" as types
import "checker.pxl" as checker
import "pixel_ast.pxl" as pixels
fn main() {
 let source = jsonDecode[layout.Source](input())
 let checked = checker.Check(types.Analyze(bindings.Bind(project.Build(source, map[layout.Source]{}))))
 print(pixels.Build(checked))
}'''
        driver = compile_project(files)[1].bytecode
        with patch('pixellang.parser.parse', side_effect=AssertionError('host pixel parser')), patch(
            'pixellang.semantics.Checker.check', side_effect=AssertionError('host checker')):
            emitted = VM(driver, **SELFHOST_HEAP_LIMITS, input_text=json.dumps(source), max_steps=100000000, max_depth=1024).run()[0]
        self.assertEqual(emitted['diagnostics'], [])
        self.assertTrue(any(pixel['rgba'] == rgba('type', 'map') for pixel in emitted['source']['pixels']))
        result = self.compile(emitted['source'])
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(VM(result['bytecode']).run(), [42])
