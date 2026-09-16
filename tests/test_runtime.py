import unittest

from pixellang.model import PixelError
from pixellang.project import compile_project
from pixellang.vm import VM


def machine(text, **kwargs):
    _, compiled = compile_project({"main.pxl": text})
    return VM(compiled.bytecode, **kwargs)


class RuntimeMemoryTests(unittest.TestCase):
    def test_warm_builtin_signature_checks_values_and_changed_types(self):
        vm = machine('fn main() {}')
        numbers = vm.allocate('int[]', [1], None)
        vm.builtin('append', [numbers, 2], None)
        with self.assertRaisesRegex(PixelError, 'element type'):
            vm.builtin('append', [numbers, True], None)
        self.assertEqual(vm.sequence(numbers, None), [1, 2])
        # Same signature, different contents: validation of byte ranges must run
        # even after a successful call warmed the signature cache.
        vm.builtin('crc32', [numbers], None)
        vm.builtin('append', [numbers, 256], None)
        with self.assertRaisesRegex(PixelError, 'Byte values'):
            vm.builtin('crc32', [numbers], None)
        other = vm.allocate('str[]', ['a'], None)
        vm.builtin('append', [other, 'b'], None)
        self.assertEqual(vm.sequence(other, None), ['a', 'b'])

    def test_allocation_churn_reclaims_unreachable_arrays(self):
        vm = machine(
            "fn main() {\nvar i = 0\nwhile i < 2000 { let scratch = [i, i + 1]; i += 1 }\nprint(i)\n}"
        )
        self.assertEqual(vm.run(), [2000])
        self.assertLess(len(vm.heap), 130)
        self.assertGreater(vm.collections, 0)
        self.assertEqual(vm.next_address, 2000)

    def test_nested_alias_and_transient_roots_survive_collection(self):
        vm = machine(
            "fn main() {\nlet root = [[42]]\nlet alias = root[0]\nvar i = 0\nwhile i < 1000 { let scratch = [[i]]; i += 1 }\nprint(alias[0])\nprint(root[0][0])\n}"
        )
        self.assertEqual(vm.run(), [42, 42])

    def test_checkpoints_resume_identically_across_gc(self):
        vm = machine(
            "fn main() {\nvar i = 0\nwhile i < 400 { let a = [i]; i += 1 }\nprint(i)\n}"
        )
        for _ in range(1000):
            vm.step(snapshot=False)
        saved = vm.checkpoint()
        vm.run()
        final = vm.state()
        vm.restore_checkpoint(saved)
        vm.run()
        self.assertEqual(vm.state(), final)

    def test_cancel_is_checked_between_instructions(self):
        vm = machine("fn main() {\nwhile true { let x = [1] }\n}")
        vm.step(snapshot=False)
        vm.cancel()
        with self.assertRaisesRegex(PixelError, "cancelled"):
            vm.step(snapshot=False)
        self.assertTrue(vm.halted)


class RecordingBudgetTests(unittest.TestCase):
    def make(self, **kwargs):
        from pixellang.temporal import Timeline

        source, _ = compile_project(
            {
                "main.pxl": "fn main() {\nvar i = 0\nwhile i < 700 { let a = [i]; i += 1 }\nprint(i)\n}"
            }
        )
        return Timeline(source, **kwargs)

    def test_checkpoint_seek_is_bounded_and_exact(self):
        timeline = self.make(checkpoint_interval=64)
        timeline.run()
        for z in [5000, 100, 3000, 2020]:
            result = timeline.seek(z)
            self.assertLess(timeline.last_seek_replayed, 64)
            reference = timeline.fresh_vm()
            for _ in range(z):
                reference.step(snapshot=False)
            self.assertEqual(result, reference.state())

    def test_budgets_and_atomic_pause(self):
        timeline = self.make(checkpoint_budget=4096, recording_budget=8192)
        with self.assertRaisesRegex(PixelError, "Recording budget"):
            timeline.run()
        self.assertLessEqual(timeline.checkpoint_bytes, 4096)
        self.assertLessEqual(timeline.events.bytes, 8192)
        self.assertEqual(timeline.vm.steps, len(timeline.events))
        self.assertFalse(timeline.vm.halted)
        saved = timeline.vm.state()
        with self.assertRaises(PixelError):
            timeline.advance()
        self.assertEqual(timeline.vm.state(), saved)
        timeline.cancel()
        self.assertTrue(timeline.vm.halted)
        self.assertLessEqual(timeline.events.bytes, 8192)

    def test_journal_shares_instruction_provenance(self):
        timeline = self.make()
        timeline.run()
        self.assertIsInstance(timeline.events.records, bytearray)
        self.assertEqual(len(timeline.events.records), 12 * len(timeline.events))
        self.assertLess(timeline.events.bytes, 3_000_000)
        from pixellang.recording import footprint

        journal = timeline.events
        measured = footprint(
            (journal.records, journal.functions, journal.function_ids, journal.errors)
        )
        self.assertLessEqual(measured, journal.bytes)
        self.assertEqual(journal[-1]["steps"], timeline.vm.steps)
        with self.assertRaises(IndexError):
            _ = timeline.events[-len(timeline.events) - 1]

    def test_cancel_recording_roundtrip_and_past_state(self):
        from pixellang.temporal import Timeline

        timeline = self.make(checkpoint_interval=64, recording_budget=100_000)
        for _ in range(100):
            timeline.advance()
        before = timeline.vm.state()
        timeline.cancel()
        self.assertTrue(timeline.vm.halted)
        self.assertIn("cancelled", timeline.vm.error["message"])
        self.assertEqual(timeline.seek(100), before)
        restored = Timeline.restore(timeline.document())
        self.assertEqual(restored.vm.state(), timeline.vm.state())
        self.assertEqual(restored.seek(100), before)
        self.assertEqual(restored.events.budget, 100_000)
        self.assertEqual(restored.checkpoint_interval, 64)

    def test_service_cancellation_preserves_history(self):
        from pixellang.workstation import Workstation

        service = Workstation()
        built = service.request(
            "build", {"files": {"main.pxl": "fn main() {\nwhile true { let i = 1 }\n}"}}
        )
        args = {"session": built["session"]}
        running = service.request("run", args)
        cancelled = service.request("cancel", args)
        self.assertTrue(cancelled["state"]["halted"])
        self.assertEqual(cancelled["length"], running["length"] + 1)
        earlier = service.request("seek", {**args, "z": 10})
        self.assertFalse(earlier["state"]["halted"])
        self.assertGreater(earlier["recording"]["journal_bytes"], 0)
