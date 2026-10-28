"""Journal sessions survive serialized RPC thread changes and close reliably."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from pixellang.workstation import Workstation
from tests.test_native_events_runtime import application_program


class ApplicationWorkstationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        compiler = Mock()
        compiler.request.return_value = dict(result=dict(bytecode=application_program()))
        with patch('pixellang.workstation.CompilerClient', return_value=compiler):
            self.service = Workstation()
        self.addCleanup(self.service.close)

    def open(self, name):
        return self.service.request('application-open', dict(files={'main.pxl': 'fixture'},
            journalPath=str(Path(self.temp.name) / (name + '.pxjournal'))))

    def test_worker_thread_changes_close_and_resume(self):
        with ThreadPoolExecutor(max_workers=1) as pool:
            opened = pool.submit(self.open, 'orders').result(timeout=10)
        session = opened['session']
        app = self.service.application_sessions[session]
        self.assertIsNotNone(opened['journal'])
        self.assertEqual(self.service.request('application-step', dict(session=session))['cursor'], 1)
        self.service.request('application-close', dict(session=session))
        self.assertTrue(app.runtime.journal.closed)
        self.assertEqual(self.open('orders')['cursor'], 1)

    def test_eviction_and_shutdown_close_journals(self):
        first = self.open('0')['session']
        journal = self.service.application_sessions[first].runtime.journal
        for i in range(1, 5): self.open(str(i))
        self.assertNotIn(first, self.service.application_sessions)
        self.assertTrue(journal.closed)
        remaining = [app.runtime.journal for app in self.service.application_sessions.values()]
        self.service.close()
        self.assertTrue(all(journal.closed for journal in remaining))
        self.assertEqual(self.service.application_sessions, {})
