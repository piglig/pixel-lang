import unittest

from pixellang.model import PixelError
from pixellang.project import compile_project
from pixellang.temporal import Timeline


class TemporalTests(unittest.TestCase):
    def make(self, text, **kwargs):
        source, _ = compile_project({"main.pxl": text})
        return Timeline(source, **kwargs)

    def test_xyz_and_loop_revisits(self):
        timeline = self.make(
            "fn main() {\nvar i = 0\nwhile i < 3 { i += 1 }\nprint(i)\n}"
        )
        self.assertEqual(timeline.run()["output"], [3])
        self.assertEqual(
            [e["z"] for e in timeline.events], list(range(1, len(timeline.events) + 1))
        )
        points = [tuple(e["points"][0][:2]) for e in timeline.events if e["points"]]
        self.assertLess(len(set(points)), len(points))
        self.assertTrue(
            all(p[2] == e["z"] for e in timeline.events for p in e["points"])
        )

    def test_seek_heap_and_output_are_exact(self):
        timeline = self.make(
            "fn main() {\nlet a = [1]\nprint(a)\na[0] = 9\nprint(a)\n}"
        )
        snapshots = [timeline.vm.state()]
        while not timeline.vm.halted:
            timeline.advance()
            snapshots.append(timeline.vm.state())
        for z in [len(snapshots) - 1, 0, 4, 2, 7, 1]:
            self.assertEqual(timeline.seek(z), snapshots[z])
        self.assertEqual(timeline.seek(len(timeline.events))["output"], [[1], [9]])

    def test_input_and_restore(self):
        timeline = self.make("fn main() {\nprint(input())\n}", input_text="世界")
        timeline.run()
        timeline.seek(1)
        restored = Timeline.restore(timeline.document())
        self.assertEqual(restored.events, timeline.events)
        self.assertEqual(restored.cursor, 1)
        self.assertEqual(restored.seek(len(restored.events))["output"], ["世界"])

    def test_failure_is_a_terminal_time_layer(self):
        timeline = self.make("fn main() {\nprint(1 / 0)\n}")
        state = timeline.run()
        self.assertIsNotNone(state["error"])
        self.assertIn("error", timeline.events[-1])
        self.assertIsNone(timeline.advance())
        self.assertIsNone(timeline.seek(0)["error"])
        self.assertEqual(Timeline.restore(timeline.document()).vm.state(), state)

    def test_budget_and_bounds(self):
        timeline = self.make("fn main() {\nwhile true { print(1) }\n}", max_steps=8)
        timeline.run()
        self.assertEqual(len(timeline.events), 9)
        for z in [-1, 10, True]:
            with self.assertRaises(PixelError):
                timeline.seek(z)

    def test_sparse_volume_uses_source_colors(self):
        timeline = self.make("fn main() {\nlet n = 1\nprint(n + 2)\n}")
        timeline.run()
        volume = timeline.volume()
        colors = {tuple(p["position"]): p["rgba"] for p in timeline.source["pixels"]}
        self.assertTrue(volume["voxels"])
        for voxel in volume["voxels"]:
            x, y, z = voxel["position"]
            self.assertEqual(voxel["rgba"], colors[x, y])
            self.assertIn([x, y, z], timeline.events[z - 1]["points"])
        self.assertEqual(timeline.volume(1, 0)["voxels"], [])

    def test_multifile_volume_separates_module_planes(self):
        source, _ = compile_project(
            {
                "main.pxl": 'import "math.pxl"\nfn main() {\nprint(math.double(2))\n}',
                "math.pxl": "export fn double(n: int) -> int = n * 2",
            }
        )
        timeline = Timeline(source)
        timeline.run()
        self.assertEqual(
            {v["module"] for v in timeline.volume()["voxels"]}, {"main", "1"}
        )
