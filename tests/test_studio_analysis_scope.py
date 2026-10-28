"""Interactive queries analyze dependencies; project edits retain all callers."""
import unittest
from unittest.mock import patch

from pixellang.workstation import Workstation


class StudioAnalysisScopeTests(unittest.TestCase):
    def test_local_queries_reuse_dependencies_but_references_cover_callers(self):
        studio = Workstation()
        self.addCleanup(studio.close)
        files = {
            'main.pxl': 'fn main() {}',
            'lib.pxl': 'export fn value() -> int = 42',
            'caller.pxl': 'import "lib.pxl" as lib\nfn use() -> int = lib.value()',
            'other.pxl': 'fn other() -> int = 1',
        }
        args = dict(files=files, source='lib.pxl', offset=files['lib.pxl'].index('value'))
        with patch.object(studio.editor_compiler, 'request', wraps=studio.editor_compiler.request) as compile:
            self.assertEqual([x['name'] for x in studio.request('document-symbols', args)], ['value'])
            self.assertEqual(set(studio.editor_index.paths), {'lib.pxl'})
            self.assertEqual(compile.call_count, 1)
            changed = {**files, 'other.pxl': 'fn other() -> int = 2'}
            studio.request('document-symbols', dict(args, files=changed))
            self.assertEqual(compile.call_count, 1)
            self.assertEqual(studio.editor_index.files['other.pxl'], changed['other.pxl'])
            refs = studio.request('references', dict(args, files=changed))
            self.assertEqual({x['source'] for x in refs}, {'lib.pxl', 'caller.pxl'})
            edits = studio.request('rename', dict(args, files=changed, new_name='answer'))
            self.assertEqual({x['span']['source'] for x in edits}, {'lib.pxl', 'caller.pxl'})

    def test_import_changes_invalidate_local_analysis(self):
        studio = Workstation()
        self.addCleanup(studio.close)
        files = {'main.pxl': 'import "lib.pxl" as lib\nfn main() { let value = lib.value() }',
                 'lib.pxl': 'export fn value() -> int = 42'}
        args = dict(files=files, source='main.pxl', offset=files['main.pxl'].index('value ='))
        with patch.object(studio.editor_compiler, 'request', wraps=studio.editor_compiler.request) as compile:
            self.assertEqual(studio.request('hover', args)['label'], 'value: int')
            changed = {**files, 'lib.pxl': 'export fn value() -> string = "text"'}
            self.assertEqual(studio.request('hover', dict(args, files=changed))['label'], 'value: string')
            self.assertEqual(compile.call_count, 2)


if __name__ == '__main__':
    unittest.main()
