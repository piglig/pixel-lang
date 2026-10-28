import unittest
import hashlib
import json
import tempfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from pixellang.bootstrap_image import run_vm
from pixellang.compiler_client import CompilerClient
from pixellang.vm import VM


class BootstrapImageHarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def compile(self, files):
        return self.compiler.request('compile', files=files)['result']['bytecode']

    def test_image_heap_budget_reaches_both_vm_stages_and_manifest(self):
        from pixellang.bootstrap_image import main
        code = self.compile({'main.pxl': 'effect fn main() { print(42) }'})
        with tempfile.TemporaryDirectory() as directory:
            seed = Path(directory) / 'seed.json'
            seed.write_text(json.dumps(code))
            output = Path(directory) / 'evidence'
            budgets = []
            def native_stage(vm, report, **kwargs):
                budgets.append(vm.max_heap_items)
                if len(budgets) == 1:
                    (output / 'compiler.png').write_bytes(b'fixture')
                    return [{'diagnostics': [], 'bytes': 7}]
                return []
            with patch('sys.argv', ['bootstrap_image', '--seed', str(seed), '--output', str(output), '--heap-items', '96000000']), \
                 patch('pixellang.bootstrap_image.run_vm', side_effect=native_stage), \
                 patch('pixellang.bootstrap_image.read_bytecode_stream', return_value={'diagnostics': [], 'bytecode': code}), \
                 patch('pixellang.bootstrap_image.compile_with', return_value=(code, 1)), \
                 redirect_stdout(StringIO()):
                main()
            self.assertEqual(budgets, [96_000_000, 96_000_000])
            manifest = json.loads((output / 'manifest.json').read_text())
            self.assertEqual(manifest['profile']['max_heap_items'], 96_000_000)
            self.assertTrue(json.loads((output / 'summary.json').read_text())['verified'])

    def test_invalid_image_heap_budget_is_rejected_before_output_creation(self):
        from pixellang.bootstrap_image import main
        for budget in ('0', '-1'):
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'evidence'
                with patch('sys.argv', ['bootstrap_image', '--output', str(output), '--heap-items', budget]), \
                     self.assertRaises(SystemExit):
                    main()
                self.assertFalse(output.exists())

    def test_fixed_point_starts_from_compiled_seed_without_host_frontend(self):
        from pixellang.bootstrap import main
        code = self.compile({'main.pxl': 'effect fn main() { print(42) }'})
        with tempfile.TemporaryDirectory() as directory:
            seed = Path(directory) / 'seed.json'
            seed.write_text(json.dumps(code))
            output = Path(directory) / 'evidence'
            with patch('sys.argv', ['bootstrap', '--seed', str(seed), '--output', str(output)]), patch('pixellang.bootstrap.compile_with', return_value=(code, 1)) as compile_native, redirect_stdout(StringIO()):
                main()
            self.assertEqual(compile_native.call_count, 6)
            self.assertTrue(json.loads((output / 'summary.json').read_text())['fixedPointVerified'])
            manifest = json.loads((output / 'manifest.json').read_text())
            self.assertEqual(manifest['seedSha256'], hashlib.sha256(seed.read_bytes()).hexdigest())
            self.assertEqual(compile_native.call_args_list[0].args[0], code)

    def test_fixed_point_rejects_seed_changed_during_run(self):
        from pixellang.bootstrap import main
        code = self.compile({'main.pxl': 'effect fn main() { print(42) }'})
        with tempfile.TemporaryDirectory() as directory:
            seed = Path(directory) / 'seed.json'
            seed.write_text(json.dumps(code))
            output = Path(directory) / 'evidence'
            def compile_native(*args, **kwargs):
                seed.write_text(json.dumps(code) + '\n')
                return code, 1
            with patch('sys.argv', ['bootstrap', '--seed', str(seed), '--output', str(output)]), patch('pixellang.bootstrap.compile_with', side_effect=compile_native), redirect_stdout(StringIO()), self.assertRaisesRegex(ValueError, 'inputs changed'):
                main()
            self.assertFalse(json.loads((output / 'summary.json').read_text())['fixedPointVerified'])

    def test_progress_and_output_preserve_vm_execution(self):
        code = self.compile({'main.pxl': 'effect fn main() { print(42) }'})
        events = []
        vm = VM(code)
        self.assertEqual(run_vm(vm, events.append, interval=1), [42])
        self.assertTrue(events[-1]['halted'])
        self.assertEqual(events[-1]['steps'], vm.steps)
        self.assertTrue(all(a['steps'] <= b['steps'] for a, b in zip(events, events[1:])))

    def test_host_frontends_are_blocked_during_steps(self):
        code = self.compile({'main.pxl': 'fn main() {}'})
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
        code = self.compile({'main.pxl': 'fn main() { while true {} }'})
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
