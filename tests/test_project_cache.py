import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pixellang.compiler_client import CompilerClient
from pixellang.fileaccess import FileAccess
from pixellang.project_cache import compile_bytecode, directory, load
from pixellang.vm import VM
from pixellang.workspace import Workspace


class ProjectCacheTests(unittest.TestCase):
    def test_selected_function_has_an_independent_cache_identity(self):
        files = {'main.pxl': 'effect fn test_first(){print(1)}\neffect fn test_second(){print(2)}'}
        with CompilerClient() as compiler:
            for name, expected in [('test_first', 1), ('test_second', 2)]:
                code = compile_bytecode(compiler, files, 'main.pxl', entry_function=name)
                self.assertEqual(VM(code).run(), [expected])
            with patch.object(compiler, 'request', side_effect=AssertionError('cache miss')):
                for name, expected in [('test_second', 2), ('test_first', 1)]:
                    code = compile_bytecode(compiler, files, 'main.pxl', entry_function=name)
                    self.assertEqual(VM(code).run(), [expected])

    def test_non_object_cache_documents_are_ignored(self):
        folder = self.root / 'malformed'
        folder.mkdir()
        for value in ([], None, 42, 'invalid'):
            with self.subTest(value=value):
                (folder / 'key.json').write_text(json.dumps(value))
                self.assertIsNone(load(folder, 'key'))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {'PIXELLANG_CACHE_DIR': str(self.root / 'cache'),
                                                  'PIXELLANG_DISABLE_CACHE': '0'})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_hit_reexecutes_with_new_arguments_and_source_change_recompiles(self):
        project = self.root / 'project'
        project.mkdir()
        (project / 'pixel.toml').write_text('format=1\n[project]\nname="cache-test"\nentry="main.pxl"\n')
        source = project / 'main.pxl'
        source.write_text('effect fn main(){ print(programArgs()) }')
        self.assertEqual(Workspace(project).run(arguments=['first'])['output'], [['first']])
        with patch.object(CompilerClient, 'request', side_effect=AssertionError('unexpected compilation')):
            self.assertEqual(Workspace(project).run(arguments=['second'])['output'], [['second']])
        source.write_text('effect fn main(){ print(42) }')
        self.assertEqual(Workspace(project).run()['output'], [42])

    def test_corrupt_cache_is_rebuilt_and_disabled_cache_is_not_read(self):
        files = {'main.pxl': 'effect fn main(){print(42)}'}
        with CompilerClient() as compiler:
            code = compile_bytecode(compiler, files, 'main.pxl')
            self.assertEqual(VM(code).run(), [42])
            cached = next(directory().glob('*.json'))
            cached.write_text('{broken')
            code = compile_bytecode(compiler, files, 'main.pxl')
            self.assertEqual(VM(code).run(), [42])
            self.assertIn('sha256', json.loads(cached.read_text()))
            with patch.dict(os.environ, {'PIXELLANG_DISABLE_CACHE': '1'}), patch.object(
                    compiler, 'request', side_effect=RuntimeError('compilation required')):
                with self.assertRaisesRegex(RuntimeError, 'compilation required'):
                    compile_bytecode(compiler, files, 'main.pxl')

    def test_cache_directory_failure_does_not_fail_compilation(self):
        cache = self.root / 'cache'
        cache.write_text('not a directory')
        with CompilerClient() as compiler:
            code = compile_bytecode(compiler, {'main.pxl': 'effect fn main(){print(7)}'}, 'main.pxl')
        self.assertEqual(VM(code).run(), [7])

    def test_hit_reads_current_files_and_keeps_capability_checks(self):
        project = self.root / 'project'
        project.mkdir()
        (project / 'pixel.toml').write_text('format=1\n[project]\nname="cache-test"\nentry="main.pxl"\n')
        (project / 'main.pxl').write_text('effect fn main(){match readText("data.txt") {.Ok(text)=>{print(text)},.Err(error)=>{print(error.code)}}}')
        data = project / 'data.txt'
        data.write_text('first')
        self.assertEqual(Workspace(project).run(file_access=FileAccess(project))['output'], ['first'])
        data.write_text('second')
        with patch.object(CompilerClient, 'request', side_effect=AssertionError('unexpected compilation')):
            self.assertEqual(Workspace(project).run(file_access=FileAccess(project))['output'], ['second'])
            denied = Workspace(project).run(file_access=FileAccess())
            self.assertEqual(denied['output'], ['io.access_denied'])
