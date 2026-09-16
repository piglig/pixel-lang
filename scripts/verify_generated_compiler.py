"""Run language and real-project regressions using an existing compiler artifact.

Run with the repository Python environment. No compiler is built by this runner.
Whole-compiler checking/lowering belongs to the separate bootstrap gate; all other
checker and IR tests are reused without changing their assertions.
"""

import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pixellang.bootstrap_image import HOST_STAGES
from pixellang.regression import run
from pixellang.parallel_regression import run_parallel, shards, timing_costs
from pixellang.vm import validate_bytecode
from tests.test_selfhost_checker import SelfHostedCheckerTests
from tests.test_selfhost_ir import SelfHostedIRTests
from tests.test_selfhost_recovery import SelfHostedRecoveryTests
from tests.test_compiler_service import CompilerServiceTests
from tests.test_incremental_ir import IncrementalIRTests


def digest(data):
    return hashlib.sha256(data).hexdigest()


def artifact_suite(code):
    suite = unittest.TestSuite()
    omitted = {
        'test_own_compiler_expression_check': 'Covered by the full-source bootstrap gate',
        'test_compiler_source_lowers_to_ir': 'Covered by the full-source bootstrap gate',
    }

    def setup(cls):
        cls.bytecode = code
        cls.code = code

    for base in (SelfHostedCheckerTests, SelfHostedIRTests, SelfHostedRecoveryTests,
                 CompilerServiceTests, IncrementalIRTests):
        generated = type('Generated' + base.__name__, (base,), {
            '__module__': __name__, 'setUpClass': classmethod(setup),
        })
        for name in unittest.defaultTestLoader.getTestCaseNames(generated):
            if name not in omitted:
                suite.addTest(generated(name))
    return suite, omitted


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler', type=Path, required=True)
    parser.add_argument('--sources', type=Path, required=True,
                        help='sources.json saved by the compiler bootstrap run')
    parser.add_argument('--output', type=Path, required=True,
                        help='New evidence directory, never overwritten')
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--timings', type=Path, help='Previous test evidence used only to balance shards')
    parser.add_argument('--timeout', type=float, default=300)
    parser.add_argument('--test', action='append', dest='tests', help=argparse.SUPPRESS)
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 1 <= args.workers <= 16 or args.timeout <= 0:
        parser.error('Workers must be 1..16 and timeout must be positive')
    artifact = args.compiler.read_bytes()
    source_bytes = args.sources.read_bytes()
    runner_bytes = Path(__file__).read_bytes()
    code = json.loads(artifact)
    validate_bytecode(code)
    sources = json.loads(source_bytes)
    current = {p.name: p.read_text() for p in sorted((ROOT / 'selfhost').glob('*.pxl'))}
    suite, omitted = artifact_suite(code)
    if args.tests:
        selected = [test for test in suite if test.id() in args.tests]
        if len(selected) != len(args.tests):
            parser.error('Unknown or duplicate generated-compiler test ID')
        suite = unittest.TestSuite(selected)
    selection = dict(
        compiler=str(args.compiler.resolve()), compilerSha256=digest(artifact),
        sources=str(args.sources.resolve()), sourcesSha256=digest(source_bytes),
        runnerSha256=digest(runner_bytes), sourceSnapshotMatchesCurrent=sources == current,
        omitted=omitted, tests=[test.id() for test in suite],
    )
    with ExitStack() as stack:
        # Also block imported aliases used by the original setUpClass methods.
        for target in (*HOST_STAGES, 'pixellang.project.compile_project',
                       'tests.test_selfhost_checker.compile_project',
                       'tests.test_selfhost_ir.compile_project',
                       'tests.test_compiler_service.compile_project',
                       'tests.test_incremental_ir.compile_project',
                       'tests.test_selfhost_recovery.compile_project'):
            stack.enter_context(patch(target, side_effect=AssertionError(
                'Generated-artifact regression cannot use the host compiler')))
        if args.child:
            summary = run(suite, args.output, ROOT, selection=selection)
        else:
            def command(group, output):
                return [sys.executable, str(Path(__file__).resolve()), '--child', '--compiler',
                        str(args.compiler.resolve()), '--sources', str(args.sources.resolve()),
                        '--output', str(output), *[arg for name in group for arg in ('--test', name)]]
            costs = timing_costs(args.timings) if args.timings else None
            selection['timingCosts'] = costs
            summary = run_parallel(shards(selection['tests'], args.workers, costs), args.output,
                                   ROOT, command, timeout=args.timeout, selection=selection)
    summary['artifactUnchanged'] = args.compiler.read_bytes() == artifact
    summary['snapshotUnchanged'] = args.sources.read_bytes() == source_bytes
    summary['runnerUnchanged'] = Path(__file__).read_bytes() == runner_bytes
    summary['sourceSnapshotMatchesCurrent'] = sources == current
    summary['verified'] = bool(summary['verified'] and all(summary[key] for key in (
        'artifactUnchanged', 'snapshotUnchanged', 'runnerUnchanged',
        'sourceSnapshotMatchesCurrent')) and not summary['skipped']
        and not summary['expectedFailures'])
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary), flush=True)
    raise SystemExit(0 if summary['verified'] else 1)


if __name__ == '__main__':
    main()
