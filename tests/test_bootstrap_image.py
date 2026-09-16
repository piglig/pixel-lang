import unittest
from unittest.mock import patch

from pixellang.bootstrap_image import run_vm
from pixellang.project import compile_project
from pixellang.vm import VM


class BootstrapImageHarnessTests(unittest.TestCase):
    def test_progress_and_output_preserve_vm_execution(self):
        code = compile_project({'main.pxl': 'fn main() { print(42) }'})[1].bytecode
        events = []
        vm = VM(code)
        self.assertEqual(run_vm(vm, events.append, interval=1), [42])
        self.assertTrue(events[-1]['halted'])
        self.assertEqual(events[-1]['steps'], vm.steps)
        self.assertTrue(all(a['steps'] <= b['steps'] for a, b in zip(events, events[1:])))

    def test_host_frontends_are_blocked_during_steps(self):
        code = compile_project({'main.pxl': 'fn main() {}'})[1].bytecode
        vm = VM(code)
        def delegated(**kwargs):
            from pixellang.text import parse_text
            parse_text('fn main() {}')
        with patch.object(vm, 'step', side_effect=delegated), self.assertRaisesRegex(AssertionError, 'Host frontend'):
            run_vm(vm, lambda event: None)

    def test_invalid_progress_interval(self):
        for value in (0, -1, True):
            with self.assertRaises(ValueError):
                run_vm(None, lambda event: None, interval=value)

    def test_stage_deadline_stops_execution(self):
        code = compile_project({'main.pxl': 'fn main() { while true {} }'})[1].bytecode
        vm = VM(code)
        with patch('pixellang.bootstrap_image.time.monotonic', side_effect=[0, 2]):
            with self.assertRaises(TimeoutError):
                run_vm(vm, lambda event: None, interval=1, timeout=1)
        self.assertEqual(vm.steps, 1)

    def test_invalid_stage_deadline(self):
        from pixellang.bootstrap import compile_with
        for value in (0, -1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                run_vm(None, lambda event: None, timeout=value)
            with self.assertRaises(ValueError):
                compile_with(None, {}, timeout=value)
