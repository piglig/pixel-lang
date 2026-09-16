import unittest

from pixellang.profiling import ProfileVM, ScopedProfileVM, run_profile
from pixellang.project import compile_project
from pixellang.vm import VM


class ProfilingTests(unittest.TestCase):
    def test_scoped_timing_restores_callers_and_preserves_results(self):
        code = compile_project({'main.pxl': 'fn child() -> int { var n=0; while n<300 { let a=[n]; n+=1 }; return n }\nfn main(){print(child());print(42)}'})[1].bytecode
        names = list(code['functions'])
        vm = ScopedProfileVM(code, function_phases={names[0]: 'child', names[1]: 'parent'})
        normal = VM(code)
        self.assertEqual(vm.run(), normal.run())
        summary = vm.phase_summary()
        self.assertEqual(sum(p['steps'] for p in summary['phases'].values()), normal.steps)
        self.assertAlmostEqual(sum(p['gcSeconds'] for p in summary['phases'].values()), summary['gcSeconds'])
        self.assertGreater(summary['phases']['child']['gcSeconds'], 0)
        self.assertGreater(summary['phases']['parent']['steps'], 0)

    def test_scoped_timing_survives_exception_unwind(self):
        code = compile_project({'main.pxl': 'fn child(){fail("x")}\nfn main(){try{child()}catch e{print(e.message)};print(42)}'})[1].bytecode
        vm = ScopedProfileVM(code, function_phases={list(code['functions'])[0]: 'child'})
        self.assertEqual(vm.run(), VM(code).run())
        self.assertEqual(sum(p['steps'] for p in vm.phase_summary()['phases'].values()), vm.steps)

    def test_instrumentation_preserves_execution_and_gc(self):
        code = compile_project({'main.pxl': 'fn main() { var n = 0; while n < 300 { let a = [n]; n += 1 }; print(n) }'})[1].bytecode
        normal = VM(code)
        observed = ProfileVM(code)
        events = []
        self.assertEqual(run_profile(observed, report=events.append, interval=100), normal.run())
        self.assertEqual(observed.steps, normal.steps)
        self.assertGreater(events[-1]['gcCount'], 0)
        self.assertTrue(events[-1]['sourceSamples'])

    def test_timeout_keeps_partial_profile(self):
        code = compile_project({'main.pxl': 'fn main() { while true {} }'})[1].bytecode
        events = []
        with self.assertRaises(TimeoutError):
            run_profile(ProfileVM(code), timeout=.001, interval=10, report=events.append)
        self.assertFalse(events[-1]['halted'])

    def test_phase_markers_are_removed_from_results(self):
        code = compile_project({'main.pxl': 'fn main() { print("__pixel_profile_phase__:work"); print(42) }'})[1].bytecode
        events = []
        self.assertEqual(run_profile(ProfileVM(code), report=events.append), [42])
        self.assertEqual([p['phase'] for p in events[-1]['phases']], ['input', 'work'])

    def test_optional_source_spans(self):
        code = compile_project({'main.pxl': 'fn main() { print(42) }'})[1].bytecode
        for function in code['functions'].values():
            for instruction in function['instructions']:
                instruction['span'] = None
        events = []
        self.assertEqual(run_profile(ProfileVM(code), interval=1, report=events.append), [42])
        self.assertIn('?', events[-1]['sourceSamples'])
