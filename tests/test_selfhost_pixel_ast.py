import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from tests.native_compiler import compile_native_sources, native_driver
from tests.pixel_language_cases import PixelLanguageCases
from pixellang.bootstrap import read_bytecode_stream
from pixellang.fileaccess import FileAccess
from pixellang.vm import VM
from tests.selfhost_runtime import SELFHOST_HEAP_LIMITS

ROOT = Path(__file__).resolve().parents[1]


class SelfHostedPixelASTTests(PixelLanguageCases, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bytecode = compile_native_sources({p.name: p.read_text() for p in (ROOT / 'selfhost').glob('*.pxl')})
        cls.run_native = staticmethod(cls.enterClassContext(native_driver(cls.bytecode)))

    def events(self, request):
        return self.run_native(request, steps=100_000_000, output_chars=64_000_000)

    def reference_events(self, request):
        return VM(self.bytecode, **SELFHOST_HEAP_LIMITS, input_text=json.dumps(request),
                  max_steps=100_000_000, max_depth=1024,
                  max_output_chars=64_000_000).run()

    def encode(self, source):
        files = source if isinstance(source, dict) else {'main.pxl': source}
        with patch('pixellang.text.parse_text', side_effect=AssertionError('delegated')):
            return self.events(dict(source='main.pxl', text='', operation='pixels', files=files))[0]

    def compile_pixels(self, source):
        document = dict(source)
        modules = document.pop('bundle', {'modules': {}})['modules']
        result = read_bytecode_stream(self.events(dict(source='program.pixel', text='',
            operation='pixel-bytecode-stream', document=document, modules=modules)))
        self.assertEqual(result['diagnostics'], [])
        return result['bytecode']

    def test_pixel_pipeline_matches_reference_vm(self):
        # Keep an independent execution path for encoding, rejection and decode.
        # Large project cases below use the native driver and independent outputs.
        for source in ('effect fn main() { print("界😀"); print(6 * 7) }',
                       'effect fn main() { print(missing) }'):
            with self.subTest(source=source):
                request = dict(source='main.pxl', text='', operation='pixels',
                               files={'main.pxl': source})
                expected = self.reference_events(request)
                actual = self.events(request)
                self.assertEqual(actual, expected)
                if actual[0]['diagnostics']:
                    continue
                document = dict(actual[0]['source'])
                modules = document.pop('bundle')['modules']
                request = dict(source='program.pixel', text='', operation='pixel-bytecode-stream',
                               document=document, modules=modules)
                reference = read_bytecode_stream(self.reference_events(request))
                result = read_bytecode_stream(self.events(request))
                self.assertEqual(result, reference)
                self.assertEqual(result['diagnostics'], [])
                self.assertEqual(VM(result['bytecode']).run(), ['界😀', 42])

    def assert_execution(self, source, expected):
        result = self.encode(source)
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(result['source']['metadata'], {})
        self.assertTrue(result['source']['spatial']['links'])
        self.assertEqual(VM(self.compile_pixels(result['source']), max_steps=1_000_000).run(), expected)
        return result['source']

    def test_real_data_processing_projects(self):
        library = {'std/' + p.name: p.read_text() for p in (ROOT / 'pixellang' / 'stdlib').glob('*.pxl')}
        for name in ('callback-analysis', 'ledger', 'log-analysis'):
            with self.subTest(project=name), tempfile.TemporaryDirectory() as directory:
                root = ROOT / 'examples' / name
                files = {p.name: p.read_text() for p in root.glob('*.pxl')}
                files.update(library)
                result = self.encode(files)
                self.assertEqual(result['diagnostics'], [])
                fixtures = root if name == 'callback-analysis' else root / 'fixtures'
                request = fixtures / ('input.json' if name == 'callback-analysis' else 'config.json')
                expected = json.loads((fixtures / 'expected.json').read_text())
                bytecode = self.compile_pixels(result['source'])
                vm = VM(bytecode, input_text=request.read_text(), max_steps=10_000_000,
                        file_access=FileAccess(fixtures, Path(directory)))
                self.assertEqual(vm.run(), expected)
                if name != 'callback-analysis':
                    report = Path(directory) / json.loads(request.read_text())['output']
                    self.assertEqual(json.loads(report.read_text()), expected[0])

    def test_rejected_sources_are_not_executable(self):
        result = self.encode('effect fn main() { print(missing) }')
        self.assertTrue(result['diagnostics'])
        self.assertEqual(result['source']['pixels'], [])
        result = self.encode({'main.pxl': 'import "lib.pxl" as lib\neffect fn main() { lib.work() }'})
        self.assertTrue(result['diagnostics'])
        self.assertEqual(result['source']['pixels'], [])
        self.assertEqual(result['source']['bundle']['modules'], {})
