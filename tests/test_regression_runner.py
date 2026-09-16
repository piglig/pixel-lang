import io
import json
import tempfile
import unittest
from pathlib import Path

from pixellang.regression import run


class RegressionRunnerTests(unittest.TestCase):
    def test_immediate_error_and_subtest_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'evidence'
            class Cases(unittest.TestCase):
                def test_error(self):
                    raise ValueError('exact error evidence')
                def test_subtest(self):
                    with self.subTest(value=1):
                        self.assertEqual(1, 2)
                def test_z_observes_previous_errors(self):
                    events = [json.loads(line) for line in (output / 'events.jsonl').read_text().splitlines()]
                    self.assertTrue(any(e['kind'] == 'error' for e in events))
                    self.assertTrue(any(e['kind'] == 'subtest-failure' for e in events))
            stream = io.StringIO()
            summary = run(unittest.defaultTestLoader.loadTestsFromTestCase(Cases), output, root, stream)
            self.assertEqual((summary['tests'], summary['errors'], summary['failures']), (3, 1, 1))
            self.assertFalse(summary['verified'])
            self.assertIn('exact error evidence', stream.getvalue())
            self.assertEqual(json.loads((output / 'summary.json').read_text()), summary)

    def test_source_changes_invalidate_otherwise_passing_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'selfhost').mkdir()
            source = root / 'selfhost/main.pxl'
            source.write_text('before')
            class Case(unittest.TestCase):
                def runTest(self):
                    source.write_text('after')
            summary = run(unittest.TestSuite([Case()]), root / 'evidence', root, io.StringIO())
            self.assertTrue(summary['testsPassed'])
            self.assertFalse(summary['verified'])
            self.assertEqual(summary['changedFiles'], ['selfhost/main.pxl'])

    def test_clean_run_and_existing_evidence_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'evidence'
            summary = run(unittest.TestSuite([unittest.FunctionTestCase(lambda: None)]), output, root, io.StringIO())
            self.assertTrue(summary['verified'])
            with self.assertRaises(FileExistsError):
                run(unittest.TestSuite(), output, root, io.StringIO())

    def test_empty_suite_is_not_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertFalse(run(unittest.TestSuite(), root / 'evidence', root, io.StringIO())['verified'])
