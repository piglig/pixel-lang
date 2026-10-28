import unittest
from unittest.mock import patch
from contextlib import ExitStack

from pixellang.workstation import Workstation
from pixellang.model import PixelError


class WorkstationTests(unittest.TestCase):
    def setUp(self):
        self.service = Workstation()
        self.addCleanup(self.service.close)

    def test_build_run_seek_and_recover(self):
        built = self.service.request(
            "build",
            {
                "files": {
                    "main.pxl": "effect fn main() {\nlet values = [3, 1, 2]\nprint(values)\n}"
                }
            },
        )
        session = built["session"]
        result = self.service.request("run", {"session": session})
        self.assertEqual(result["output"], ["[3, 1, 2]"])
        self.assertFalse(result['projection_ready'])
        self.assertFalse(result['volume']['voxels'])
        before = self.service.request('recording', {'session': session})
        projected = self.service.request('inspect-projection', {'session': session})
        self.assertTrue(projected['projection_ready'])
        self.assertTrue(projected['volume']['voxels'])
        self.assertEqual(self.service.request('recording', {'session': session}), before)
        initial = self.service.request("seek", {"session": session, "z": 0})
        self.assertEqual(initial["output"], [])
        self.assertFalse(initial["state"]["halted"])
        self.assertNotIn("image", built)
        image = self.service.request("inspect-image", {"session": session})["image"]
        with patch.object(self.service.compiler, 'request', side_effect=AssertionError('PNG regenerated')):
            self.assertEqual(self.service.request("inspect-image", {"session": session})["image"], image)
        restored = self.service.request("recover", {"image": image})
        self.assertIn("let values", restored["files"]["main.pxl"])
        record = self.service.request("recording", {"session": session})
        with ExitStack() as stack:
            for target in ('pixellang.picture.encode_picture',):
                stack.enter_context(patch(target,side_effect=AssertionError('Host replay path')))
            replay = self.service.request("restore", {"document": record})
        self.assertEqual(replay['cursor'], 0)
        self.assertEqual(replay['project'], built['source_project'])
        self.assertEqual(self.service.request('run', {'session':replay['session']})['output'], ['[3, 1, 2]'])

    def test_nominal_variable_names_do_not_require_pixel_projection(self):
        built=self.service.request('build',{'files':{
            'main.pxl':'import "lib.pxl" as lib\nrecord Box { value: int }\neffect fn main(){let local=Box{value:42};let remote=lib.Make();print(local)}',
            'lib.pxl':'export record Other { value: int }\nexport fn Make()->Other=Other{value:7}'}})
        session=built['session']
        previous=built
        while True:
            state=self.service.request('step',{'session':session})
            if state['state']['halted']:break
            previous=state
        self.service.request('seek',{'session':session,'z':previous['cursor']})
        values=self.service.request('variables',{'session':session,'z':previous['cursor'],'path':['frame',0]})['variables']
        self.assertEqual([value['type'] for value in values],['Box','lib.Other'])
        self.assertIsNone(self.service.sessions[session].source)

    def test_build_never_requests_png(self):
        with patch.object(self.service.compiler, 'request', wraps=self.service.compiler.request) as request:
            built = self.service.request('build', {'files': {'main.pxl': 'fn main() {}'}})
        self.assertNotIn('image', built)
        self.assertIsNone(self.service.sessions[built['session']].compiled.image)
        self.assertTrue(request.call_args_list)
        for call in request.call_args_list:
            self.assertNotIn(call.args[0], ('export-png', 'export-debug-png', 'export-pixel-png', 'debug-pixels', 'debug-bundle'))
            self.assertFalse(call.kwargs.get('includePNG', False))

    def test_build_keeps_artifact_on_server_until_explicit_inspection(self):
        built = self.service.request('build', {'files': {'main.pxl': 'effect fn main(){print(42)}'}})
        self.assertEqual(built['inspection'], {'ir': None})
        self.assertTrue(all(value >= 0 for value in built['timings'].values()))
        with patch.object(self.service.compiler, 'request', side_effect=AssertionError('Unexpected recompile')):
            artifact = self.service.request('inspect-artifact', {'session': built['session']})
            self.assertTrue(artifact['bytecode']['functions'])
            self.assertTrue(artifact['debug'])
            self.assertEqual(self.service.request('run', {'session': built['session']})['output'], ['42'])

    def test_cancelled_image_can_retry_without_rebuilding_or_changing_snapshot(self):
        from types import SimpleNamespace
        import base64
        snapshot = {'files': {'main.pxl': 'fn main() {}'}, 'entry': 'main.pxl'}
        artifact = SimpleNamespace(image=None)
        self.service.sessions['session'] = SimpleNamespace(compiled=artifact, source_project=snapshot)
        cancelled = lambda: True
        with patch.object(self.service.compiler, 'request', side_effect=PixelError('service', 'Cancelled')) as request:
            with self.assertRaises(PixelError):
                self.service.request('inspect-image', {'session': 'session'}, cancelled=cancelled)
            self.assertIs(request.call_args.kwargs['cancelled'], cancelled)
        self.assertIsNone(artifact.image)
        with patch.object(self.service.compiler, 'request', return_value={'result': {'image': b'png'}}) as request:
            image = self.service.request('inspect-image', {'session': 'session'})
            self.assertEqual(base64.b64decode(image['image']), b'png')
            self.assertEqual(request.call_args.kwargs['files'], snapshot['files'])
            self.assertEqual(self.service.request('inspect-image', {'session': 'session'}), image)
            self.assertEqual(request.call_count, 1)

    def test_restore_rejects_legacy_or_missing_source_before_compilation(self):
        cases = [None, [], {}, {'backend':'host'}, {'backend':'selfhost'},
                 {'backend':'selfhost','source_project':{'files':[]}}]
        with patch.object(self.service.compiler,'debug_project',side_effect=AssertionError('Unexpected compilation')):
            for document in cases:
                with self.subTest(document=document), self.assertRaises(PixelError):
                    self.service.request('restore',{'document':document})
        self.assertEqual(self.service.sessions,{})

    def test_breakpoint_stops_before_line(self):
        built = self.service.request(
            "build",
            {"files": {"main.pxl": "effect fn main() {\nlet answer = 42\nprint(answer)\n}"}},
        )
        result = self.service.request(
            "continue", {"session": built["session"], "breakpoints": {"main.pxl": [3]}}
        )
        self.assertEqual(result["location"]["start"]["line"], 3)
        self.assertEqual(result["output"], [])

    def test_large_integer_display_preserved(self):
        built = self.service.request(
            "build",
            {"files": {"main.pxl": "effect fn main() {\nprint(9223372036854775807)\n}"}},
        )
        result = self.service.request("run", {"session": built["session"]})
        self.assertEqual(result["output"], ["9223372036854775807"])

    def test_breakpoint_does_not_repeat_after_function_return(self):
        built = self.service.request(
            "build",
            {
                "files": {
                    "main.pxl": "fn answer(n: int) -> int = n + 2\neffect fn main() {\nlet x = 40\nprint(answer(x))\n}"
                }
            },
        )
        args = {"session": built["session"], "breakpoints": {"main.pxl": [4]}}
        paused = self.service.request("continue", args)
        self.assertTrue(paused["line_start"])
        self.assertEqual(paused["output"], [])
        end = self.service.request("continue", args)
        self.assertEqual(end["output"], ["42"])
        self.assertTrue(end["state"]["halted"])
