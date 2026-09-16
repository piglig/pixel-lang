import random
import unittest
from itertools import pairwise

from pixellang.project import compile_project
from pixellang.temporal import Timeline


class ReplayCoverageTests(unittest.TestCase):
    def test_thinned_checkpoints_cover_history_and_preserve_exact_states(self):
        source, _ = compile_project(
            {
                "main.pxl": """fn main() {
 var count = 0
 let next = fn() { count += 1 }
 while count < 300 { next() }
 print(count)
}"""
            }
        )
        timeline = Timeline(source, checkpoint_interval=16, checkpoint_budget=48_000)
        rng = random.Random(42)
        positions = set(rng.sample(range(1, 2000), 60))
        expected = {}
        while not timeline.vm.halted:
            timeline.advance()
            if timeline.cursor in positions:
                expected[timeline.cursor] = timeline.vm.state()
        self.assertEqual(timeline.vm.output, [300])
        self.assertLessEqual(timeline.checkpoint_bytes, timeline.checkpoint_budget)
        self.assertEqual(
            timeline.checkpoint_bytes,
            sum(size for _, size in timeline.checkpoints.values()),
        )
        keys = sorted(timeline.checkpoints)
        self.assertGreater(len(keys), 4)
        self.assertLess(keys[1], len(timeline.events) // 2)
        # Enough retained state must cover the entire run, not just its tail.
        self.assertLess(
            max(b - a for a, b in pairwise(keys)), len(timeline.events) // 2
        )
        for z in sorted(expected, reverse=True):
            timeline.replay_vm = None
            timeline.replay_z = 0
            self.assertEqual(timeline.seek(z), expected[z])
        restored = Timeline.restore(timeline.document())
        for z in list(expected)[::7]:
            self.assertEqual(restored.seek(z), expected[z])

    def test_tiny_budget_and_large_heap_do_not_break_replay(self):
        source, _ = compile_project(
            {
                "main.pxl": """fn main() {
 let values: [int] = []
 var i = 0
 while i < 100 { append(values, i); i += 1 }
 print(len(values))
}"""
            }
        )
        timeline = Timeline(source, checkpoint_interval=8, checkpoint_budget=1024)
        timeline.run()
        self.assertEqual(timeline.vm.output, [100])
        self.assertLessEqual(timeline.checkpoint_bytes, 1024)
        timeline.seek(10)
        self.assertEqual(timeline.seek(len(timeline.events))["output"], [100])
