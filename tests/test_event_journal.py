"""Durability, bounded replay, and failure atomicity of native event journals."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from pixellang.event_journal import JournaledApplicationRuntime, encoded
from pixellang.model import PixelError
from pixellang.vm import VM
from pixellang.native_events import NativeApplicationRuntime
from tests.test_native_events_runtime import application_program


class EventJournalTests(unittest.TestCase):
    def test_cold_recovery_executes_only_the_checkpoint_suffix(self):
        runtime = self.runtime(checkpoint_interval=4)
        runtime.enqueue({'variant': 'Set', 'fields': {'value': 7}}, tick=30)
        for _ in range(10): runtime.step()
        pending, sequence = list(runtime.pending), runtime.sequence
        runtime.close()
        original_step = NativeApplicationRuntime.step
        with patch.object(NativeApplicationRuntime, 'step', autospec=True, side_effect=original_step) as step:
            recovered = self.runtime(checkpoint_interval=4)
            self.assertEqual(step.call_count, 2)
        self.assertEqual((recovered.completed, recovered.view()), (10, 9))
        self.assertEqual((recovered.pending, recovered.sequence), (pending, sequence))
        self.assertEqual(recovered.step(), 10)
        with patch.object(NativeApplicationRuntime, 'step', autospec=True, side_effect=original_step) as step:
            self.assertEqual(recovered.inspect(9).view(), 8)
            self.assertEqual(step.call_count, 1)
        with patch.object(NativeApplicationRuntime, 'step', autospec=True, side_effect=original_step) as step:
            prefix, event = recovered.before_event(10)
            self.assertEqual(prefix.completed, 9)
            self.assertEqual(event['completed'], 10)
            self.assertEqual(step.call_count, 1)

    def test_checkpoint_does_not_skip_prefix_integrity_validation(self):
        runtime = self.runtime(checkpoint_interval=4)
        for _ in range(10): runtime.step()
        runtime.close()
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.execute("UPDATE commands SET payload='{}' WHERE revision=1")
        with self.assertRaisesRegex(PixelError, 'digest'): self.runtime()

    def test_checkpoint_and_event_commit_are_one_transaction(self):
        runtime = self.runtime(checkpoint_interval=1)
        before = runtime.vm.checkpoint()
        runtime.journal.connection.execute("CREATE TRIGGER fault BEFORE INSERT ON checkpoints BEGIN SELECT RAISE(ABORT, 'disk failure'); END")
        with self.assertRaises(sqlite3.DatabaseError): runtime.step()
        self.assertEqual(runtime.completed, 0)
        self.assertEqual(runtime.vm.checkpoint(), before)
        self.assertEqual(self.runtime().completed, 0)

    def test_typed_checkpoint_rejects_forged_output_even_with_updated_checksum(self):
        runtime = self.runtime(checkpoint_interval=1)
        runtime.step()
        runtime.close()
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            payload, anchor = db.execute('SELECT payload,anchor FROM checkpoints').fetchone()
            document = json.loads(payload)
            document['latest']['output'] = 'not an integer'
            payload = encoded(document)
            db.execute('UPDATE checkpoints SET payload=?,digest=?',
                       (payload.decode(), hashlib.sha256(anchor.encode() + payload).hexdigest()))
        with self.assertRaises(PixelError): self.runtime()

    def test_checkpoint_retention_and_small_budget_fall_back_to_replay(self):
        runtime = self.runtime(checkpoint_interval=1)
        for _ in range(20): runtime.step()
        self.assertEqual(runtime.journal.connection.execute('SELECT count(*) FROM checkpoints').fetchone()[0], 8)
        runtime.close()
        recovered = self.runtime(max_checkpoint_bytes=1)
        self.assertEqual(recovered.view(), 19)
        self.assertEqual(recovered.step(), 20)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'application.pxjournal'

    def runtime(self, **options):
        runtime = JournaledApplicationRuntime(VM(application_program(), max_heap_items=128), self.path, **options)
        self.addCleanup(runtime.close)
        return runtime

    def test_recovery_preserves_queue_order_discards_and_native_state(self):
        runtime = self.runtime()
        runtime.enqueue({'variant': 'Set', 'fields': {'value': 9223372036854775807}}, tick=1)
        discarded = runtime.enqueue({'variant': 'Set', 'fields': {'value': 9}}, tick=2)
        runtime.discard(discarded)
        runtime.enqueue({'variant': 'Set', 'fields': {'value': 7}}, tick=99)
        for _ in range(10): runtime.step()
        before = runtime.vm.checkpoint()
        state = (runtime.completed, runtime.sequence, runtime.tick, runtime.pending)
        runtime.close()
        recovered = self.runtime()
        self.assertEqual(recovered.vm.checkpoint(), before)
        self.assertEqual((recovered.completed, recovered.sequence, recovered.tick, recovered.pending), state)
        self.assertEqual(recovered.view(), 8)
        self.assertEqual(recovered.step(), 9)

    def test_paged_history_and_isolated_inspection_leave_frontier_unchanged(self):
        runtime = self.runtime()
        for _ in range(150): runtime.step()
        before = runtime.vm.checkpoint()
        revision = runtime.journal.revision
        self.assertEqual(runtime.history(70, 2), [dict(index=71, tick='70', kind='tick'),
                                                 dict(index=72, tick='71', kind='tick')])
        inspected = runtime.inspect(70)
        self.assertEqual(inspected.view(), 69)
        self.assertEqual(inspected.completed, 70)
        self.assertEqual(runtime.vm.checkpoint(), before)
        self.assertEqual(runtime.journal.revision, revision)
        self.assertEqual(runtime.step(), 150)
        self.assertIsNone(runtime.inspect(0).view())

    def test_forward_inspection_replays_only_new_events_and_isolates_returned_values(self):
        runtime = self.runtime()
        for _ in range(100): runtime.step()
        original_step = NativeApplicationRuntime.step
        with patch.object(NativeApplicationRuntime, 'step', autospec=True, side_effect=original_step) as step:
            inspected = runtime.inspect(70)
            self.assertEqual(step.call_count, 70)
            step.reset_mock()
            inspected.step()  # An isolated caller cannot mutate the cached prefix.
            step.reset_mock()
            self.assertEqual(runtime.inspect(71).view(), 70)
            self.assertEqual(step.call_count, 1)
            step.reset_mock()
            self.assertEqual(runtime.inspect(20).view(), 19)
            self.assertEqual(step.call_count, 20)
            step.reset_mock()
            self.assertEqual(runtime.inspect(25).view(), 24)
            self.assertEqual(step.call_count, 5)
        self.assertEqual(runtime.completed, 100)

    def test_inspection_suffix_includes_input_acceptance_and_discard(self):
        runtime = self.runtime()
        runtime.step()
        sequence = runtime.enqueue({'variant': 'Set', 'fields': {'value': 1}})
        runtime.enqueue({'variant': 'Set', 'fields': {'value': 2}})
        runtime.discard(sequence)
        runtime.step()
        runtime.inspect(1)
        inspected = runtime.inspect(2)
        self.assertEqual(inspected.pending, [])
        self.assertEqual(inspected.sequence, runtime.sequence)
        self.assertEqual(inspected.vm.checkpoint(), runtime.vm.checkpoint())

    def test_failed_suffix_keeps_previous_cache_and_cache_budget_is_optional(self):
        runtime = self.runtime()
        for _ in range(10): runtime.step()
        previous = runtime.inspect(5)
        cache = runtime.inspection_cache
        db = runtime.journal.connection
        payload = db.execute('SELECT payload FROM commands WHERE revision=8').fetchone()[0]
        db.execute("UPDATE commands SET payload='{}' WHERE revision=8")
        with self.assertRaisesRegex(PixelError, 'digest'): runtime.inspect(9)
        self.assertIs(runtime.inspection_cache, cache)
        self.assertEqual(previous.completed, 5)
        db.execute('UPDATE commands SET payload=? WHERE revision=8', (payload,))
        self.assertEqual(runtime.inspect(6).view(), 5)
        runtime.close()
        tiny = self.runtime(max_inspection_bytes=1)
        self.assertEqual(tiny.inspect(5).view(), 4)
        self.assertIsNone(tiny.inspection_cache)
        self.assertEqual(tiny.step(), 10)

    def test_streaming_replay_passes_memory_recorder_limit(self):
        runtime = self.runtime()
        for _ in range(4100): runtime.step()
        runtime.close()
        recovered = self.runtime()
        self.assertEqual(recovered.completed, 4100)
        self.assertEqual(recovered.step(), 4100)
        self.assertFalse(hasattr(recovered, 'records'))

    def test_failed_sql_commit_rolls_back_step_and_requires_reopen(self):
        runtime = self.runtime()
        runtime.step()
        before = runtime.vm.checkpoint()
        runtime.journal.connection.execute("CREATE TRIGGER fault BEFORE INSERT ON commands BEGIN SELECT RAISE(ABORT, 'disk failure'); END")
        with self.assertRaises(sqlite3.DatabaseError): runtime.step()
        self.assertEqual(runtime.completed, 1)
        self.assertEqual(runtime.vm.checkpoint(), before)
        with self.assertRaisesRegex(PixelError, 'closed'): runtime.step()
        recovered = self.runtime()
        self.assertEqual(recovered.completed, 1)
        self.assertEqual(recovered.vm.checkpoint(), before)

    def test_failed_input_write_restores_queue_and_sequence(self):
        runtime = self.runtime()
        before = runtime.vm.checkpoint()
        runtime.journal.connection.execute("CREATE TRIGGER fault BEFORE INSERT ON commands BEGIN SELECT RAISE(ABORT, 'disk failure'); END")
        with self.assertRaises(sqlite3.DatabaseError):
            runtime.enqueue({'variant': 'Set', 'fields': {'value': 7}})
        self.assertEqual((runtime.pending, runtime.sequence, runtime.pending_bytes), ([], 0, 0))
        self.assertEqual(runtime.vm.checkpoint(), before)
        self.assertEqual(self.runtime().pending, [])

    def test_stale_writer_cannot_fork_journal(self):
        first, second = self.runtime(), self.runtime()
        first.enqueue({'variant': 'Set', 'fields': {'value': 7}})
        with self.assertRaisesRegex(PixelError, 'another session'): second.step()
        self.assertEqual(second.completed, 0)
        restored = self.runtime()
        self.assertEqual(len(restored.pending), 1)
        self.assertEqual(restored.completed, 0)

    def test_failed_discard_write_keeps_accepted_input(self):
        runtime = self.runtime()
        sequence = runtime.enqueue({'variant': 'Set', 'fields': {'value': 7}})
        pending, size = list(runtime.pending), runtime.pending_bytes
        runtime.journal.connection.execute("CREATE TRIGGER fault BEFORE INSERT ON commands BEGIN SELECT RAISE(ABORT, 'disk failure'); END")
        with self.assertRaises(sqlite3.DatabaseError): runtime.discard(sequence)
        self.assertEqual((runtime.pending, runtime.pending_bytes), (pending, size))
        self.assertEqual(self.runtime().pending, pending)

    def test_uncommitted_transaction_is_discarded_after_process_kill(self):
        script = '''
import sys
from pixellang.event_journal import JournaledApplicationRuntime
from pixellang.vm import VM
from tests.test_native_events_runtime import application_program
runtime = JournaledApplicationRuntime(VM(application_program(), max_heap_items=128), sys.argv[1])
runtime.step()
db = runtime.journal.connection
db.execute('BEGIN IMMEDIATE')
db.execute("INSERT INTO commands VALUES (2, '{}', 'unfinished', NULL)")
print('transaction-open', flush=True)
sys.stdin.readline()
'''
        child = subprocess.Popen([sys.executable, '-c', script, str(self.path)], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'transaction-open')
            child.kill()
            child.communicate(timeout=10)
        finally:
            if child.poll() is None: child.kill()
            child.communicate(timeout=10)
        recovered = self.runtime()
        self.assertEqual(recovered.completed, 1)
        self.assertEqual(recovered.step(), 1)

    def test_corruption_and_mismatched_program_are_rejected(self):
        runtime = self.runtime()
        runtime.step()
        runtime.close()
        with self.assertRaisesRegex(PixelError, 'does not match'):
            JournaledApplicationRuntime(VM(application_program(), max_heap_items=128), self.path,
                                        configuration={'intervalNanoseconds': 1})
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.execute("UPDATE commands SET payload='{}' WHERE revision=1")
        with self.assertRaisesRegex(PixelError, 'digest'): self.runtime()

    def test_byte_budget_failure_leaves_application_unchanged(self):
        runtime = self.runtime(max_journal_bytes=1)
        before = runtime.vm.checkpoint()
        with self.assertRaisesRegex(PixelError, 'byte budget'): runtime.step()
        self.assertEqual(runtime.completed, 0)
        self.assertEqual(runtime.vm.checkpoint(), before)
        self.assertEqual(runtime.journal.revision, 0)

    def test_process_exit_without_close_recovers_committed_commands(self):
        script = '''
import os, sys
from pixellang.event_journal import JournaledApplicationRuntime
from pixellang.vm import VM
from tests.test_native_events_runtime import application_program
runtime = JournaledApplicationRuntime(VM(application_program(), max_heap_items=128), sys.argv[1])
runtime.enqueue({'variant': 'Set', 'fields': {'value': 7}}, tick=50)
for _ in range(5): runtime.step()
os._exit(0)
'''
        result = subprocess.run([sys.executable, '-c', script, str(self.path)],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        restored = self.runtime()
        self.assertEqual(restored.completed, 5)
        self.assertEqual(restored.view(), 4)
        self.assertEqual(len(restored.pending), 1)
        self.assertEqual(restored.step(), 5)
