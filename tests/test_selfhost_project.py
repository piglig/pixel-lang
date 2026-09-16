import json
import unittest
from pathlib import Path
from unittest.mock import patch

from pixellang.project import compile_project
from pixellang.vm import VM
from tests.selfhost_runtime import (SELFHOST_HEAP_LIMITS, SELFHOST_MAX_STEPS, SELFHOST_MAX_OUTPUT_CHARS)

ROOT = Path(__file__).resolve().parents[1]


class SelfHostedProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = {p.name: p.read_text() for p in (ROOT / 'selfhost').glob('*.pxl')}
        cls.bytecode = compile_project(cls.files)[1].bytecode

    def build(self, files, entry='main.pxl'):
        request = {'operation': 'project', 'source': entry, 'text': '', 'files': files}
        vm = VM(self.bytecode, **SELFHOST_HEAP_LIMITS, input_text=json.dumps(request), max_steps=SELFHOST_MAX_STEPS, max_depth=1024,
                max_output_chars=SELFHOST_MAX_OUTPUT_CHARS)
        with patch('pixellang.project.build_project', side_effect=AssertionError('delegated')), patch(
            'pixellang.text.parse_text', side_effect=AssertionError('delegated')
        ), patch('pixellang.text.lex', side_effect=AssertionError('delegated')):
            return vm.run()[0]

    def test_own_project_import_order_and_exports(self):
        # Audit both compiler and separately executable image-worker entry graphs.
        files = dict(self.files)
        files["main.pxl"] += '\nimport "image_worker.pxl" as imageWorker\n'
        result = self.build(files)
        self.assertEqual(result['diagnostics'], [])
        modules = result['modules']
        indexes = {module['path']: i for i, module in enumerate(modules)}
        self.assertEqual(set(indexes), set(self.files))
        for module in modules:
            for imported in module['imports']:
                self.assertLess(indexes[imported['target']], indexes[module['path']])
        model = next(m for m in modules if m['path'] == 'model.pxl')
        self.assertTrue(all(d['exported'] for d in model['declarations']))
        main = modules[-1]
        self.assertEqual(main['path'], 'main.pxl')
        self.assertFalse(next(d for d in main['declarations'] if d['name'] == 'main')['exported'])

    def test_normalization_diamond_and_standard_module(self):
        result = self.build({
            'app/main.pxl': 'import "../lib/a.pxl" as a\nimport "../lib/b.pxl" as b\nfn main() {}',
            'lib/a.pxl': 'import "./shared.pxl"\nexport fn A() {}',
            'lib/b.pxl': 'import "shared.pxl"\nimport "std/text.pxl" as text\nexport fn B() {}',
            'lib/shared.pxl': 'export record Shared { value: int }',
            'std/text.pxl': 'export fn Text() {}',
        }, 'app/./main.pxl')
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual([m['path'] for m in result['modules']], [
            'lib/shared.pxl', 'lib/a.pxl', 'std/text.pxl', 'lib/b.pxl', 'app/main.pxl'
        ])
        self.assertEqual(result['modules'][1]['imports'][0]['alias'], 'shared')

    def test_missing_cycle_and_escape_locations(self):
        for files, message, source, start in [
            ({'main.pxl': '\nimport "missing.pxl"\nfn main() {}'}, 'Module not found', 'main.pxl', 1),
            ({'main.pxl': 'import "a.pxl"\nfn main() {}', 'a.pxl': '\nimport "main.pxl"'}, 'Cyclic import', 'a.pxl', 1),
            ({'main.pxl': 'import "../escape.pxl"\nfn main() {}'}, 'Invalid import path', 'main.pxl', 0),
        ]:
            with self.subTest(message=message):
                errors = self.build(files)['diagnostics']
                self.assertEqual(len(errors), 1)
                self.assertIn(message, errors[0]['message'])
                self.assertEqual((errors[0]['source'], errors[0]['start']), (source, start))
                self.assertEqual(errors[0]['line'], 2 if start else 1)

    def test_rejects_duplicate_paths_names_and_non_declarations(self):
        for files, message in [
            ({'main.pxl': 'fn main() {}', './main.pxl': 'fn main() {}'}, 'Duplicate normalized path'),
            ({'main.pxl': 'fn main() {}\nfn main() {}'}, 'Duplicate module name'),
            ({'main.pxl': 'import "a.pxl" as main\nfn main() {}', 'a.pxl': ''}, 'Duplicate module name'),
            ({'main.pxl': 'let value = 1\nfn main() {}'}, 'Expected a module declaration'),
            ({'/main.pxl': ''}, 'Invalid module path'),
        ]:
            with self.subTest(message=message):
                self.assertIn(message, self.build(files)['diagnostics'][0]['message'])

    def test_syntax_diagnostics_keep_imported_source(self):
        result = self.build({'main.pxl': 'import "bad.pxl"\nfn main() {}', 'bad.pxl': 'fn broken( {'})
        self.assertEqual(result['diagnostics'][0]['source'], 'bad.pxl')
        self.assertEqual(result['diagnostics'][0]['phase'], 'parse')

    def test_real_projects_with_explicit_standard_library_sources(self):
        library = {
            'std/' + p.name: p.read_text()
            for p in (ROOT / 'pixellang' / 'stdlib').glob('*.pxl')
        }
        for name in ('ledger', 'log-analysis'):
            with self.subTest(project=name):
                files = {
                    p.name: p.read_text()
                    for p in (ROOT / 'examples' / name).glob('*.pxl')
                }
                files.update(library)
                result = self.build(files)
                self.assertEqual(result['diagnostics'], [])
                self.assertEqual(result['modules'][-1]['path'], 'main.pxl')
                self.assertTrue(any(m['path'].startswith('std/') for m in result['modules']))
