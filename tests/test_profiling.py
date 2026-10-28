import unittest

from pixellang.profiling import ProfileVM, ScopedProfileVM, run_profile
from pixellang.compiler_service import CompilerService
from pixellang.vm import VM


class ProfilingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerService.installed()

    def compile(self, files):
        response = self.compiler.request(dict(version=1, operation='compile', files=files))
        self.assertEqual(response['status'], 'ok', response)
        return response['result']['bytecode']

    def test_scoped_timing_restores_callers_and_preserves_results(self):
        code = self.compile({'main.pxl': 'fn child() -> int { var n=0; while n<300 { let a=[n]; n+=1 }; return n }\neffect fn main(){print(child());print(42)}'})
        phases = {name: 'child' if function['instructions'][0]['span']['start']['line'] == 1 else 'parent'
                  for name, function in code['functions'].items()}
        vm = ScopedProfileVM(code, function_phases=phases)
        normal = VM(code)
        self.assertEqual(vm.run(), normal.run())
        summary = vm.phase_summary()
        self.assertEqual(sum(p['steps'] for p in summary['phases'].values()), normal.steps)
        self.assertAlmostEqual(sum(p['gcSeconds'] for p in summary['phases'].values()), summary['gcSeconds'])
        self.assertGreater(summary['phases']['child']['gcSeconds'], 0)
        self.assertGreater(summary['phases']['parent']['steps'], 0)

    def test_scoped_timing_survives_result_propagation(self):
        code = self.compile({'main.pxl': 'fn child()->Result[int,string] { let value:Result[int,string]=.Err("x"); return .Ok(value?) }\neffect fn main(){print(match child(){ .Ok(value)=>"unexpected", .Err(error)=>error });print(42)}'})
        phases = {name: 'child' for name, function in code['functions'].items()
                  if function['instructions'][0]['span']['start']['line'] == 1}
        vm = ScopedProfileVM(code, function_phases=phases)
        self.assertEqual(vm.run(), ['x', 42])
        self.assertEqual(VM(code).run(), ['x', 42])
        self.assertGreater(vm.phase_summary()['phases']['child']['steps'], 0)
        self.assertEqual(sum(p['steps'] for p in vm.phase_summary()['phases'].values()), vm.steps)

    def test_instrumentation_preserves_execution_and_gc(self):
        code = self.compile({'main.pxl': 'effect fn main() { var n = 0; while n < 300 { let a = [n]; n += 1 }; print(n) }'})
        normal = VM(code)
        observed = ProfileVM(code)
        events = []
        self.assertEqual(run_profile(observed, report=events.append, interval=100), normal.run())
        self.assertEqual(observed.steps, normal.steps)
        self.assertGreater(events[-1]['gcCount'], 0)
        self.assertTrue(events[-1]['sourceSamples'])

    def test_timeout_keeps_partial_profile(self):
        code = self.compile({'main.pxl': 'effect fn main() { while true {} }'})
        events = []
        with self.assertRaises(TimeoutError):
            run_profile(ProfileVM(code), timeout=.001, interval=10, report=events.append)
        self.assertFalse(events[-1]['halted'])

    def test_phase_markers_are_removed_from_results(self):
        code = self.compile({'main.pxl': 'effect fn main() { print("__pixel_profile_phase__:work"); print(42) }'})
        events = []
        self.assertEqual(run_profile(ProfileVM(code), report=events.append), [42])
        self.assertEqual([p['phase'] for p in events[-1]['phases']], ['input', 'work'])

    def test_optional_source_spans(self):
        code = self.compile({'main.pxl': 'effect fn main() { print(42) }'})
        for function in code['functions'].values():
            for instruction in function['instructions']:
                instruction['span'] = None
        events = []
        self.assertEqual(run_profile(ProfileVM(code), interval=1, report=events.append), [42])
        self.assertIn('?', events[-1]['sourceSamples'])


class BenchmarkEntryPointTests(unittest.TestCase):
    """Measurement tools must start and reject ambiguous compiler selection."""

    def test_measurement_scripts_compile(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        for script in sorted((root / 'scripts').glob('*.py')):
            with self.subTest(script=script.name):
                compile(script.read_text(), str(script), 'exec')

    def test_native_measurement_help_and_incomplete_artifact_pair(self):
        from pathlib import Path
        import subprocess
        import sys
        from tempfile import TemporaryDirectory
        root = Path(__file__).resolve().parents[1]
        for name in ('benchmark_native_compile.py', 'benchmark_png_pipeline.py'):
            with self.subTest(script=name), TemporaryDirectory() as folder:
                command = [sys.executable, str(root / 'scripts' / name)]
                help_result = subprocess.run(command + ['--help'], capture_output=True, text=True, timeout=20)
                self.assertEqual(help_result.returncode, 0, help_result.stderr)
                output = Path(folder) / 'evidence'
                result = subprocess.run(command + ['--functions', '2', '--compiler', 'missing.json',
                    '--output', str(output)], capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('must be supplied together', result.stderr)
                self.assertFalse(output.exists())
