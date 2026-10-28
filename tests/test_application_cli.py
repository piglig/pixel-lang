"""CLI history selection must not impose recorder limits on ordinary execution."""
from contextlib import redirect_stdout
from io import StringIO
import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from pixellang.application_cli import main
from tests.test_native_events_runtime import application_program


class ApplicationCliTests(unittest.TestCase):
    def test_configuration_controls_logical_time_and_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            configuration = Path(directory) / 'application.json'
            recording = Path(directory) / 'events.json'
            configuration.write_text(json.dumps({'intervalNanoseconds': 17}))
            code, status = self.run_cli('--config', str(configuration), '--steps', '2',
                                        '--record', str(recording))
            self.assertEqual(code, 0, status)
            self.assertEqual(status['interval_ns'], '17')
            code, status = self.run_cli('--replay', str(recording), '--steps', '1')
            self.assertEqual(code, 0, status)
            self.assertEqual(status['interval_ns'], '17')
            self.assertEqual(status['cursor'], 3)

    def test_journal_command_resumes_without_replaying_input_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = str(Path(directory) / 'run.pxjournal')
            code, status = self.run_cli('--journal', journal, '--steps', '3')
            self.assertEqual(code, 0, status)
            self.assertEqual(status['cursor'], 3)
            code, status = self.run_cli('--journal', journal, '--steps', '2')
            self.assertEqual(code, 0, status)
            self.assertEqual((status['cursor'], status['view']), (5, 4))
            self.assertEqual(status['journal'], journal)
            code, status = self.run_cli('--journal', journal, '--steps', '0', '--seek', '2')
            self.assertEqual(code, 0, status)
            self.assertEqual((status['cursor'], status['length'], status['view']), (2, 5, 1))
            code, status = self.run_cli('--journal', journal, '--steps', '1')
            self.assertEqual(code, 0, status)
            self.assertEqual((status['cursor'], status['view']), (6, 5))
            code, status = self.run_cli('--journal', journal, '--history')
            self.assertEqual(code, 1)
            self.assertIn('cannot be combined', status['error'])

    def run_cli(self, *args):
        compiler = Mock()
        compiler.read_project.return_value = ({'main.pxl': 'fixture'}, 'main.pxl')
        compiler.request.return_value = dict(result=dict(bytecode=application_program()))
        output = StringIO()
        with patch('pixellang.application_cli.CompilerClient') as client, redirect_stdout(output):
            client.return_value.__enter__.return_value = compiler
            result = main(['main.pxl', *args])
        return result, json.loads(output.getvalue())

    def test_default_execution_passes_recording_event_limit(self):
        code, status = self.run_cli('--steps', '4100')
        self.assertEqual(code, 0, status)
        self.assertFalse(status['history'])
        self.assertEqual(status['cursor'], 4100)
        self.assertEqual(status['view'], 4099)

    def test_history_is_explicit_or_implied_by_seek(self):
        for args in (('--history',), ('--seek', '0')):
            with self.subTest(args=args):
                code, status = self.run_cli('--steps', '1', *args)
                self.assertEqual(code, 0, status)
                self.assertTrue(status['history'])
        code, status = self.run_cli('--history', '--steps', '4100')
        self.assertEqual(code, 1)
        self.assertIn('4096', status['error'])

    def test_negative_step_count_is_rejected(self):
        code, status = self.run_cli('--steps', '-1')
        self.assertEqual(code, 1)
        self.assertIn('nonnegative', status['error'])
