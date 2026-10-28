import unittest
from copy import deepcopy
from unittest.mock import patch

from pixellang.compiler_client import CompilerClient
from pixellang.model import PixelError
from pixellang.temporal import Timeline


class TemporalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def make(self, text, **kwargs):
        compiled = self.compiler.debug_project({"main.pxl": text})
        return Timeline(compiled.source, compiled=compiled, **kwargs)

    def project(self, timeline):
        snapshot = timeline.source_project
        document = self.compiler.request('debug-pixels', files=snapshot['files'], entry=snapshot['entry'])['result']['document']
        timeline.set_projection(document)

    def test_xyz_and_loop_revisits(self):
        timeline = self.make(
            "effect fn main() {\nvar i = 0\nwhile i < 3 { i += 1 }\nprint(i)\n}"
        )
        self.assertEqual(timeline.run()["output"], [3])
        self.assertEqual(
            [e["z"] for e in timeline.events], list(range(1, len(timeline.events) + 1))
        )
        self.project(timeline)
        events = [timeline.projected_event(e) for e in timeline.events]
        points = [tuple(e["points"][0][:2]) for e in events if e["points"]]
        self.assertLess(len(set(points)), len(points))
        self.assertTrue(
            all(p[2] == e["z"] for e in events for p in e["points"])
        )

    def test_projection_does_not_change_executable_identity_or_recording(self):
        timeline = self.make('effect fn main() { var a = [1]; a[0] = 9; print(a) }')
        timeline.run()
        before = timeline.document()
        events = list(timeline.events)
        state = timeline.seek(3)
        self.assertIsNone(timeline.source)
        self.project(timeline)
        self.assertTrue(timeline.volume()['voxels'])
        self.assertEqual(list(timeline.events), events)
        after = timeline.document()
        before['cursor'] = 3
        self.assertEqual(after, before)
        self.assertNotIn('source', after)
        self.assertEqual(timeline.seek(3), state)
        restored = Timeline.restore(after)
        self.assertIsNone(restored.source)
        self.assertEqual(restored.seek(3), state)
        self.assertEqual(restored.document()['compiled_sha256'], after['compiled_sha256'])

    def test_seek_heap_and_output_are_exact(self):
        timeline = self.make(
            "effect fn main() {\nvar a: [int] = [1]\nprint(a)\na[0] = 9\nprint(a)\n}"
        )
        snapshots = [timeline.vm.state()]
        while not timeline.vm.halted:
            timeline.advance()
            snapshots.append(timeline.vm.state())
        for z in [len(snapshots) - 1, 0, 4, 2, 7, 1]:
            self.assertEqual(timeline.seek(z), snapshots[z])
        self.assertEqual(timeline.seek(len(timeline.events))["output"], [[1], [9]])

    def test_replay_shares_code_but_not_execution_state(self):
        timeline = self.make("effect fn main() { print(42) }")
        replay = timeline.fresh_vm()
        self.assertIs(replay.code_image, timeline.vm.code_image)
        self.assertEqual(replay.run(), [42])
        self.assertEqual(timeline.vm.output, [])
        self.assertFalse(timeline.vm.halted)

    def test_input_and_restore(self):
        timeline = self.make("effect fn main() {\nprint(input())\n}", input_text="世界")
        timeline.run()
        timeline.seek(1)
        restored = Timeline.restore(timeline.document())
        self.assertEqual(restored.events, timeline.events)
        self.assertEqual(restored.cursor, 1)
        self.assertEqual(restored.seek(len(restored.events))["output"], ["世界"])

    def test_native_source_snapshot_is_owned_by_the_session(self):
        timeline = self.make('effect fn main() { print(42) }')
        expected = deepcopy(timeline.source_project)
        digest = timeline.document()['compiled_sha256']
        timeline.compiled.source_project['files']['main.pxl'] = 'effect fn main() { print(99) }'
        timeline.compiled.bytecode['constants'].clear()
        timeline.compiled.bytecode['functions'].clear()
        self.assertEqual(timeline.source_project, expected)
        self.assertEqual(timeline.document()['compiled_sha256'], digest)
        timeline.run()
        restored = Timeline.restore(timeline.document())
        self.assertEqual(restored.vm.output, [42])

    def test_malformed_native_snapshot_is_rejected_before_compilation(self):
        timeline = self.make('effect fn main() {}')
        for project in [None, {}, {'files': {}}, {'files': [], 'entry': 'main.pxl'},
                        {'files': {'main.pxl': 42}, 'entry': 'main.pxl'},
                        {'files': {'main.pxl': ''}, 'entry': 'missing.pxl'}]:
            document = timeline.document()
            document['source_project'] = project
            with self.subTest(project=project), patch.object(CompilerClient, 'debug_project',
                    side_effect=AssertionError('Malformed snapshot reached compiler')):
                with self.assertRaisesRegex(PixelError, 'valid source snapshot'):
                    Timeline.restore(document)

    def test_host_frontend_and_legacy_recordings_are_not_fallbacks(self):
        timeline = self.make('effect fn main() { print(42) }')
        with self.assertRaisesRegex(PixelError, 'native compiled debug artifact'):
            Timeline(timeline.source)
        for backend in [None, 'python']:
            document = timeline.document()
            document['backend'] = backend
            with self.subTest(backend=backend), patch.object(CompilerClient, 'debug_project',
                    side_effect=AssertionError('Legacy recording reached compiler')):
                with self.assertRaisesRegex(PixelError, 'native compiler recording'):
                    Timeline.restore(document)

    def test_failure_is_a_terminal_time_layer(self):
        timeline = self.make("effect fn main() {\nprint(1 / 0)\n}")
        state = timeline.run()
        self.assertIsNotNone(state["error"])
        self.assertIn("error", timeline.events[-1])
        self.assertIsNone(timeline.advance())
        self.assertIsNone(timeline.seek(0)["error"])
        self.assertEqual(Timeline.restore(timeline.document()).vm.state(), state)

    def test_budget_and_bounds(self):
        timeline = self.make("effect fn main() {\nwhile true { print(1) }\n}", max_steps=8)
        timeline.run()
        self.assertEqual(len(timeline.events), 9)
        for z in [-1, 10, True]:
            with self.assertRaises(PixelError):
                timeline.seek(z)

    def test_sparse_volume_uses_source_colors(self):
        timeline = self.make("effect fn main() {\nlet n = 1\nprint(n + 2)\n}")
        timeline.run()
        self.project(timeline)
        volume = timeline.volume()
        colors = {tuple(p["position"]): p["rgba"] for p in timeline.source["pixels"]}
        self.assertTrue(volume["voxels"])
        for voxel in volume["voxels"]:
            x, y, z = voxel["position"]
            self.assertEqual(voxel["rgba"], colors[x, y])
            self.assertIn([x, y, z], timeline.projected_event(timeline.events[z - 1])["points"])
        self.assertEqual(timeline.volume(1, 0)["voxels"], [])

    def test_multifile_volume_separates_module_planes(self):
        compiled = self.compiler.debug_project(
            {
                "main.pxl": 'import "math.pxl"\neffect fn main() {\nprint(math.double(2))\n}',
                "math.pxl": "export fn double(n: int) -> int = n * 2",
            }
        )
        timeline = Timeline(compiled.source, compiled=compiled)
        timeline.run()
        self.project(timeline)
        self.assertEqual(
            {v["module"] for v in timeline.volume()["voxels"]}, {"main", "1"}
        )
