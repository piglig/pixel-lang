import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock

from pixellang.application_session import ApplicationSession
from pixellang.model import PixelError
from tests.test_native_events_runtime import application_program


class ApplicationSessionTests(unittest.TestCase):
    def test_journal_history_inspection_does_not_branch_the_live_application(self):
        with tempfile.TemporaryDirectory() as directory:
            session = self.session(journal_path=Path(directory) / 'run.pxjournal')
            try:
                session.request('application-step', {})
                session.request('application-step', {})
                before = session.runtime.vm.checkpoint()
                past = session.request('application-seek', dict(index=1))
                self.assertEqual((past['cursor'], past['length'], past['view']), (1, 2, 0))
                self.assertEqual(session.runtime.vm.checkpoint(), before)
                with self.assertRaisesRegex(PixelError, 'latest'):
                    session.request('application-step', {})
                session.request('application-seek', dict(index=2))
                self.assertEqual(session.request('application-step', {})['view'], 2)
            finally:
                session.close()

    def session(self, **options):
        compiler = Mock()
        compiler.request.return_value = dict(result=dict(bytecode=application_program()))
        return ApplicationSession(compiler, files={'main.pxl': 'native test fixture'}, **options)

    def test_execution_session_reports_live_state_without_history(self):
        session = self.session(history=False)
        self.assertFalse(session.status()['history'])
        self.assertIsNone(session.status()['tick'])
        session.request('application-step', {})
        status = session.request('application-step', {})
        self.assertEqual((status['cursor'], status['length'], status['tick']), (2, 2, '1'))
        self.assertEqual(status['view'], 1)
        self.assertEqual(status['events'], [])
        for method in ('application-seek', 'application-recording', 'application-restore'):
            with self.assertRaisesRegex(PixelError, 'History was not enabled'):
                session.request(method, {})
        with self.assertRaisesRegex(PixelError, 'History was not enabled'):
            session.debug_event(1)

    def test_typed_input_transport_preserves_int64_and_restore_is_atomic(self):
        session = self.session()
        session.request('application-input', dict(valueText='{"variant":"Set","fields":{"value":9223372036854775807}}'))
        self.assertIn('9223372036854775807', session.status()['pending'][0]['valueText'])
        session.request('application-step', {})
        session.request('application-step', {})
        saved = session.request('application-recording', {})
        before = session.runtime.vm.checkpoint()
        session.request('application-restore', saved)
        self.assertEqual(session.runtime.vm.checkpoint(), before)
        bad = json.loads(saved['recordingText'])
        bad['events'][0]['output'] = 123
        with self.assertRaises(PixelError):
            session.request('application-restore', dict(recordingText=json.dumps(bad)))
        self.assertEqual(session.runtime.vm.checkpoint(), before)
        self.assertEqual(session.status()['cursor'], 2)

    def test_historical_view_and_explicit_discard(self):
        session = self.session()
        session.request('application-step', {})
        session.request('application-input', dict(valueText='{"variant":"Set","fields":{"value":7}}'))
        pending = session.status()['pending'][0]['sequence']
        session.request('application-seek', dict(index=0))
        with self.assertRaises(PixelError):
            session.request('application-discard', dict(sequence=pending))
        session.request('application-seek', dict(index=1))
        self.assertEqual(session.request('application-discard', dict(sequence=pending))['pending'], [])
