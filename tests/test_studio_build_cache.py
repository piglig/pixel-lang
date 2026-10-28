import time
import unittest
from unittest.mock import patch

from pixellang.model import PixelError
from pixellang.workstation import Workstation


class StudioBuildCacheTests(unittest.TestCase):
    def setUp(self):
        self.studio = Workstation()
        self.addCleanup(self.studio.close)

    def test_reuses_readonly_code_without_sharing_inputs_or_debug_state(self):
        files = {'main.pxl': 'effect fn main(){print(input())}'}
        first = self.studio.request('build', dict(files=files, input='first'))
        self.assertFalse(first['buildCacheHit'])
        old = self.studio.sessions[first['session']]
        self.assertEqual(self.studio.request('run', {'session': first['session']})['output'], ['first'])
        # Public inspection data from an earlier session cannot poison the cache.
        old.compiled.debug['functions'].clear()
        with patch.object(self.studio.compiler, 'debug_project', side_effect=AssertionError('Recompiled')):
            second = self.studio.request('build', dict(files=files, input='second'))
        fresh = self.studio.sessions[second['session']]
        self.assertTrue(second['buildCacheHit'])
        self.assertIs(fresh.code_image, old.code_image)
        self.assertIsNot(fresh.vm, old.vm)
        self.assertIsNot(fresh.events, old.events)
        self.assertEqual(second['cursor'], 0)
        self.assertEqual(second['output'], [])
        self.assertTrue(fresh.compiled.debug['functions'])
        self.assertEqual(self.studio.request('run', {'session': second['session']})['output'], ['second'])
        with self.assertRaises(TypeError):
            fresh.compiled.bytecode['functions'].clear()

    def test_source_edit_invalidates_and_cancelled_cache_hit_publishes_no_session(self):
        files = {'main.pxl': 'effect fn main(){print(1)}'}
        self.studio.request('build', {'files': files})
        count = len(self.studio.sessions)
        with self.assertRaisesRegex(PixelError, 'cancelled'):
            self.studio.request('build', {'files': files}, cancelled=lambda: True)
        with self.assertRaisesRegex(PixelError, 'deadline'):
            self.studio.request('build', {'files': files}, submitted_at=time.monotonic()-121)
        self.assertEqual(len(self.studio.sessions), count)
        changed = self.studio.request('build', {'files': {'main.pxl': 'effect fn main(){print(2)}'}})
        self.assertFalse(changed['buildCacheHit'])
        self.assertEqual(self.studio.request('run', {'session': changed['session']})['output'], ['2'])
        self.studio.close()
        self.assertIsNone(self.studio.build_cache)

    def test_build_capacity_is_separate_from_editor_budget(self):
        build = self.studio.compiler.worker.limits
        editor = self.studio.editor_compiler.worker.limits
        self.assertEqual(build.steps, 3_000_000_000)
        self.assertEqual(editor.steps, 500_000_000)
        self.assertEqual(build.seconds, editor.seconds)
        self.assertEqual(build.seconds, 120)
        self.assertEqual(build.heap_items, editor.heap_items)
        self.assertEqual(build.file_bytes, editor.file_bytes)


if __name__ == '__main__':
    unittest.main()
