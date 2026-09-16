"""Run compiler regressions with durable per-test evidence and source fingerprints."""

import argparse
import hashlib
import json
import sys
import time
import unittest
from pathlib import Path


def process_usage():
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF)
        return dict(peakRssBytes=usage.ru_maxrss * (1 if sys.platform == 'darwin' else 1024),
                    processCpuSeconds=usage.ru_utime + usage.ru_stime)
    except ImportError:
        return dict(peakRssBytes=None, processCpuSeconds=None)


def fingerprint(root):
    paths = []
    for directory in ('pixellang', 'selfhost', 'tests', 'examples'):
        base = root / directory
        if base.exists():
            paths.extend(path for path in base.rglob('*') if path.is_file()
                         and '__pycache__' not in path.parts and path.suffix != '.pyc')
    paths.extend(path for path in (root / 'pyproject.toml', root / 'setup.cfg') if path.is_file())
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths)}


class RecordedResult(unittest.TextTestResult):
    def __init__(self, stream, descriptions, verbosity, events):
        super().__init__(stream, descriptions, verbosity)
        self.events = events
        self.started = {}

    def record(self, kind, test=None, **details):
        event = dict(kind=kind, time=time.time(), **details)
        if test is not None:
            event['test'] = test.id()
        self.events.write(json.dumps(event, ensure_ascii=False) + '\n')
        self.events.flush()

    def startTest(self, test):
        self.started[test.id()] = time.monotonic()
        self.record('start', test)
        super().startTest(test)

    def stopTest(self, test):
        self.record('stop', test, seconds=time.monotonic() - self.started.pop(test.id()))
        super().stopTest(test)

    def addSuccess(self, test):
        self.record('success', test)
        super().addSuccess(test)

    def problem(self, kind, test, err):
        detail = self._exc_info_to_string(err, test)
        self.record(kind, test, traceback=detail)
        # unittest normally postpones this until the complete suite has ended.
        self.stream.writeln('\n' + test.id() + '\n' + detail)
        self.stream.flush()

    def addError(self, test, err):
        self.problem('error', test, err)
        super().addError(test, err)

    def addFailure(self, test, err):
        self.problem('failure', test, err)
        super().addFailure(test, err)

    def addSubTest(self, test, subtest, err):
        if err is not None:
            self.problem('subtest-failure' if issubclass(err[0], test.failureException)
                         else 'subtest-error', subtest, err)
        super().addSubTest(test, subtest, err)

    def addSkip(self, test, reason):
        self.record('skip', test, reason=reason)
        super().addSkip(test, reason)

    def addExpectedFailure(self, test, err):
        self.record('expected-failure', test, traceback=self._exc_info_to_string(err, test))
        super().addExpectedFailure(test, err)

    def addUnexpectedSuccess(self, test):
        self.record('unexpected-success', test)
        super().addUnexpectedSuccess(test)


def run(suite, output, root, stream=None, selection=None):
    started = time.monotonic()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    before = fingerprint(root)
    manifest = dict(files=before, selection=selection, started=time.time())
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    with (output / 'events.jsonl').open('w') as events:
        runner = unittest.TextTestRunner(
            stream=stream or sys.stderr, verbosity=1,
            resultclass=lambda *args: RecordedResult(*args, events))
        try:
            result = runner.run(suite)
        except BaseException as error:
            events.write(json.dumps(dict(kind='aborted', time=time.time(), error=repr(error))) + '\n')
            events.flush()
            raise
    after = fingerprint(root)
    changed = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
    summary = dict(tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                   skipped=len(result.skipped), expectedFailures=len(result.expectedFailures),
                   unexpectedSuccesses=len(result.unexpectedSuccesses), testsPassed=result.wasSuccessful(),
                   sourcesUnchanged=not changed, changedFiles=changed, finished=time.time(),
                   seconds=time.monotonic() - started)
    summary['verified'] = summary['testsPassed'] and summary['sourcesUnchanged'] and summary['tests'] > 0
    summary.update(process_usage())
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New evidence directory; never overwritten')
    parser.add_argument('--scope', choices=('all', 'host', 'selfhost'), default='all')
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--timeout', type=float, default=300)
    parser.add_argument('--test', action='append', dest='tests')
    parser.add_argument('--timings', type=Path, help='Prior evidence used only to balance test shards')
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    names = ['tests.' + path.stem for path in sorted((root / 'tests').glob('test_*.py'))
             if args.scope == 'all' or path.stem.startswith('test_selfhost') == (args.scope == 'selfhost')]
    if not 1 <= args.workers <= 16 or args.timeout <= 0:
        parser.error('Workers must be 1..16 and timeout must be positive')
    suite = unittest.defaultTestLoader.loadTestsFromNames(args.tests or names)
    selection = dict(scope=args.scope, modules=names, tests=args.tests)
    if args.child:
        summary = run(suite, args.output, root, selection=selection)
    else:
        from .parallel_regression import run_parallel, shards, test_ids, timing_costs
        costs = timing_costs(args.timings) if args.timings else None
        selection['timingCosts'] = costs
        def command(group, output):
            return [sys.executable, '-m', 'pixellang.regression', '--child', '--output', str(output),
                    *[arg for name in group for arg in ('--test', name)]]
        summary = run_parallel(shards(list(test_ids(suite)), args.workers, costs), args.output,
                               root, command, timeout=args.timeout, selection=selection)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    raise SystemExit(0 if summary['verified'] else 1)


if __name__ == '__main__':
    main()
