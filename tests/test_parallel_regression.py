import json
from pathlib import Path
import sys
import tempfile
import unittest

from pixellang.parallel_regression import run_parallel, shards, timing_costs

ROOT = Path(__file__).resolve().parents[1]


def command(group, output):
    return [sys.executable, '-m', 'pixellang.regression', '--child', '--output', str(output),
            *[arg for name in group for arg in ('--test', name)]]


class ParallelRegressionTests(unittest.TestCase):
    def execute(self, names):
        with tempfile.TemporaryDirectory() as directory:
            summary = run_parallel([[f'tests.regression_fixture.Cases.{name}'] for name in names],
                                   Path(directory) / 'run', ROOT, command, timeout=10)
            events = [json.loads(line) for line in (Path(directory) / 'run/events.jsonl').read_text().splitlines()]
            return summary, events

    def test_exact_coverage_and_durable_events(self):
        result, events = self.execute(['test_pass_a', 'test_pass_b'])
        self.assertTrue(result['verified'])
        self.assertEqual(result['tests'], 2)
        self.assertEqual(len([e for e in events if e['kind'] == 'success']), 2)
        self.assertEqual({e['worker'] for e in events}, {0, 1})

    def test_worker_failure_cannot_pass(self):
        result, events = self.execute(['test_pass_a', 'test_fail'])
        self.assertFalse(result['verified'])
        self.assertEqual(result['failures'], 1)
        self.assertTrue(any('worker failure evidence' in e.get('traceback', '') for e in events))

    def test_timeout_terminates_workers_and_saves_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'run'
            with self.assertRaises(TimeoutError):
                run_parallel([['tests.regression_fixture.Cases.test_wait']], output, ROOT, command, timeout=.5)
            summary = json.loads((output / 'summary.json').read_text())
            self.assertFalse(summary['verified'])
            self.assertIsNotNone(summary['workers'][0]['exitCode'])

    def test_mismatched_test_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            def wrong(group, output):
                return command(['tests.regression_fixture.Cases.test_pass_b'], output)
            result = run_parallel([['tests.regression_fixture.Cases.test_pass_a']],
                                  Path(directory) / 'run', ROOT, wrong, timeout=10)
            self.assertFalse(result['verified'])
            self.assertFalse(result['workers'][0]['complete'])

    def test_shards_have_no_missing_or_repeated_tests(self):
        ids = [str(i) for i in range(11)]
        self.assertEqual(sorted(sum(shards(ids, 4), [])), sorted(ids))
        with self.assertRaises(ValueError):
            shards(ids, 0)

    def test_cost_balancing_preserves_coverage_and_fixture_order(self):
        groups = shards(['a', 'b', 'c', 'd'], 2, {'a': 100, 'c': 90})
        self.assertEqual(sorted(sum(groups, [])), ['a', 'b', 'c', 'd'])
        self.assertFalse(any('a' in group and 'c' in group for group in groups))
        for group in groups:
            self.assertEqual(group, sorted(group))

    def test_interrupted_test_gets_conservative_cost_without_being_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            path.write_text('\n'.join(json.dumps(event) for event in (
                dict(kind='start', test='slow'), dict(kind='stop', test='slow', seconds=20),
                dict(kind='start', test='interrupted'))))
            self.assertEqual(timing_costs(directory), {'slow': 20, 'interrupted': 20})
