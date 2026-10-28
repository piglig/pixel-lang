"""Transactional, streaming journals for deterministic native applications.

The journal contains commands and output digests, never executable snapshots.
SQLite provides atomic commits; revision checks reject stale concurrent writers.
"""
from dataclasses import dataclass
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3

from .model import PixelError
from .native_events import NativeApplicationRuntime
from .recording import footprint


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def step_command(record, completed):
    return dict(operation='step', completed=completed, tick=record.tick,
                sequence=record.sequence, kind=record.kind, output=digest(record.display))


def replay_command(runtime, command):
    operation = command.get('operation')
    if operation == 'enqueue' and set(command) == {'operation', 'tick', 'sequence', 'input'}:
        sequence = runtime.enqueue(command['input'], tick=command['tick'])
        if type(command['sequence']) is not int or sequence != command['sequence']:
            raise PixelError('event', 'Journal input sequence is invalid')
    elif operation == 'discard' and set(command) == {'operation', 'sequence'}:
        runtime.discard(command['sequence'])
    elif operation == 'step' and set(command) == {'operation', 'completed', 'tick', 'sequence', 'kind', 'output'}:
        runtime.step()
        if encoded(command) != encoded(step_command(runtime.latest, runtime.completed)):
            raise PixelError('event', 'Journal event replay diverged')
    else:
        raise PixelError('event', 'Invalid journal operation')


@dataclass(frozen=True)
class JournalCursor:
    """A trusted process-local prefix already validated by scan()."""
    revision: int
    digest: str
    payload_bytes: int


class EventJournal:
    def __init__(self, path, header, *, max_bytes=1_000_000_000, max_command_bytes=128_000_000,
                 max_checkpoint_bytes=64_000_000):
        if any(type(n) is not int or n <= 0 for n in (max_bytes, max_command_bytes, max_checkpoint_bytes)):
            raise ValueError('Journal budgets must be positive integers')
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.max_command_bytes = max_command_bytes
        self.max_checkpoint_bytes = max_checkpoint_bytes
        self.connection = None
        self.closed = False
        try:
            # WorkstationLane serializes requests, but consecutive requests may
            # use different RPC worker threads. Runtime callers must serialize.
            db = self.connection = sqlite3.connect(self.path, timeout=0, isolation_level=None,
                                                   check_same_thread=False)
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not tables:
                db.execute('CREATE TABLE metadata (id INTEGER PRIMARY KEY CHECK(id=1), header TEXT NOT NULL, '
                           'revision INTEGER NOT NULL, digest TEXT NOT NULL, payload_bytes INTEGER NOT NULL)')
                db.execute('CREATE TABLE commands (revision INTEGER PRIMARY KEY, payload TEXT NOT NULL, '
                           'digest TEXT NOT NULL, event_index INTEGER UNIQUE)')
                db.execute('CREATE TABLE checkpoints (revision INTEGER PRIMARY KEY, event_index INTEGER NOT NULL, '
                           'payload TEXT NOT NULL, digest TEXT NOT NULL, anchor TEXT NOT NULL, prefix_bytes INTEGER NOT NULL)')
                db.execute('INSERT INTO metadata VALUES (1, ?, 0, ?, 0)', (encoded(header).decode(), digest(header)))
            elif tables != {'metadata', 'commands', 'checkpoints'}:
                raise PixelError('event', 'File is not a native application journal')
            row = db.execute('SELECT header, revision, digest, payload_bytes FROM metadata WHERE id=1').fetchone()
            if row is None or row[0] != encoded(header).decode():
                raise PixelError('event', 'Journal does not match this application, interval or runtime budgets')
            self.revision, self.tail, self.payload_bytes = row[1:]
            if (type(self.revision) is not int or self.revision < 0 or
                    type(self.payload_bytes) is not int or not 0 <= self.payload_bytes <= max_bytes):
                raise PixelError('event', 'Invalid journal metadata or journal budget exceeded')
            self.seed = digest(header)
            db.execute('COMMIT')
        except BaseException:
            self.close()
            raise

    def close(self):
        self.closed = True
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def append(self, command, checkpoint=None):
        if self.closed:
            raise PixelError('event', 'Journal is closed; reopen it to recover committed commands')
        payload = encoded(command)
        if len(payload) > self.max_command_bytes or self.payload_bytes + len(payload) > self.max_bytes:
            raise PixelError('event', 'Journal byte budget exceeded')
        next_digest = hashlib.sha256(self.tail.encode() + payload).hexdigest()
        try:
            checkpoint_data = encoded(checkpoint) if checkpoint is not None else None
        except RecursionError:
            checkpoint_data = None
        if checkpoint_data is not None and len(checkpoint_data) > self.max_checkpoint_bytes:
            checkpoint_data = None
        db = self.connection
        try:
            db.execute('BEGIN IMMEDIATE')
            actual = db.execute('SELECT revision, digest, payload_bytes FROM metadata WHERE id=1').fetchone()
            if actual != (self.revision, self.tail, self.payload_bytes):
                raise PixelError('event', 'Journal changed in another session; reopen before writing')
            db.execute('INSERT INTO commands VALUES (?, ?, ?, ?)',
                       (self.revision + 1, payload.decode(), next_digest, command.get('completed')))
            db.execute('UPDATE metadata SET revision=?, digest=?, payload_bytes=? WHERE id=1',
                       (self.revision + 1, next_digest, self.payload_bytes + len(payload)))
            if checkpoint_data is not None:
                checksum = hashlib.sha256(next_digest.encode() + checkpoint_data).hexdigest()
                db.execute('INSERT INTO checkpoints VALUES (?, ?, ?, ?, ?, ?)',
                           (self.revision + 1, command['completed'], checkpoint_data.decode(), checksum,
                            next_digest, self.payload_bytes + len(payload)))
                while True:
                    count, size = db.execute('SELECT count(*), coalesce(sum(length(CAST(payload AS BLOB))),0) FROM checkpoints').fetchone()
                    if count <= 8 and size <= self.max_checkpoint_bytes: break
                    db.execute('DELETE FROM checkpoints WHERE revision=(SELECT min(revision) FROM checkpoints)')
            db.execute('COMMIT')
        except BaseException:
            # A failed commit may have an uncertain outcome. Never retry using
            # this in-memory state; reopen and recover the database's committed tip.
            self.close()
            raise
        self.revision += 1
        self.tail = next_digest
        self.payload_bytes += len(payload)

    def checkpoint(self, index=None):
        """Read a data-only checkpoint bound to a committed command prefix."""
        row = self.connection.execute('SELECT revision,event_index,length(CAST(payload AS BLOB)) FROM checkpoints '
            'WHERE revision<=? AND event_index<=? ORDER BY revision DESC LIMIT 1',
            (self.revision, (1 << 63) - 1 if index is None else index)).fetchone()
        if row is None: return None
        revision, event_index, size = row
        if size > self.max_checkpoint_bytes:
            return None  # A smaller cache budget can always fall back to replay.
        payload, checksum, anchor, prefix_bytes = self.connection.execute(
            'SELECT payload,digest,anchor,prefix_bytes FROM checkpoints WHERE revision=?', (revision,)).fetchone()
        if (any(type(value) is not str for value in (payload, checksum, anchor))
                or type(prefix_bytes) is not int or not 0 <= prefix_bytes <= self.payload_bytes):
            raise PixelError('event', 'Checkpoint metadata is invalid')
        if hashlib.sha256(anchor.encode() + payload.encode()).hexdigest() != checksum:
            raise PixelError('event', 'Checkpoint digest is invalid')
        if self.connection.execute('SELECT digest FROM commands WHERE revision=?', (revision,)).fetchone() != (anchor,):
            raise PixelError('event', 'Checkpoint does not match its journal prefix')
        try:
            document = json.loads(payload)
        except (ValueError, RecursionError):
            raise PixelError('event', 'Checkpoint document is invalid') from None
        if not isinstance(document, dict) or document.get('completed') != event_index:
            raise PixelError('event', 'Checkpoint event index is invalid')
        return document, JournalCursor(revision, anchor, prefix_bytes)

    def commands(self):
        for command, _ in self.scan():
            yield command

    def scan(self, cursor=None):
        """Validate a suffix from a previously verified, process-local prefix."""
        if self.closed:
            raise PixelError('event', 'Journal is closed')
        tip = (self.revision, self.tail, self.payload_bytes)
        cursor = cursor or JournalCursor(0, self.seed, 0)
        previous, revision, size = cursor.digest, cursor.revision, cursor.payload_bytes
        if not 0 <= revision <= tip[0] or not 0 <= size <= tip[2]:
            raise PixelError('event', 'Journal cursor is outside the committed range')
        if revision:
            anchor = self.connection.execute('SELECT digest FROM commands WHERE revision=?', (revision,)).fetchone()
            if anchor != (previous,):
                raise PixelError('event', 'Journal prefix changed; reopen before inspection')
        elif previous != self.seed or size != 0:
            raise PixelError('event', 'Invalid initial journal cursor')
        rows = self.connection.execute(
            'SELECT revision, payload, digest, event_index FROM commands '
            'WHERE revision>? AND revision<=? ORDER BY revision', (revision, tip[0]))
        try:
            for index, payload, checksum, event_index in rows:
                if not isinstance(payload, str):
                    raise PixelError('event', 'Invalid journal command')
                raw = payload.encode('utf-8')
                size += len(raw)
                if len(raw) > self.max_command_bytes or size > self.max_bytes:
                    raise PixelError('event', 'Journal byte budget exceeded')
                expected = hashlib.sha256(previous.encode() + raw).hexdigest()
                if index != revision + 1 or checksum != expected:
                    raise PixelError('event', 'Journal command sequence or digest is invalid')
                command = json.loads(payload)
                if (not isinstance(command, dict) or encoded(command) != raw or
                        command.get('completed') != event_index):
                    raise PixelError('event', 'Invalid journal command encoding')
                previous, revision = checksum, index
                yield command, JournalCursor(revision, previous, size)
            if (revision, previous, size) != tip:
                raise PixelError('event', 'Journal tip does not match its committed commands')
        finally:
            rows.close()


class JournaledApplicationRuntime(NativeApplicationRuntime):
    """The native scheduler with durable input acceptance and event commits."""
    def __init__(self, vm, path, *, max_journal_bytes=1_000_000_000, max_inspection_bytes=64_000_000,
                 checkpoint_interval=128, max_checkpoint_bytes=64_000_000, **budgets):
        if vm.steps or vm.file_access.read_root is not None or vm.file_access.write_root is not None:
            raise PixelError('event', 'A journal requires a fresh VM without external file capabilities')
        if type(max_inspection_bytes) is not int or max_inspection_bytes <= 0:
            raise ValueError('Inspection cache budget must be a positive integer')
        self.max_inspection_bytes = max_inspection_bytes
        if type(checkpoint_interval) is not int or checkpoint_interval < 1:
            raise ValueError('Checkpoint interval must be positive')
        self.checkpoint_interval = checkpoint_interval
        self.inspection_cache = None
        self.startup_budget = vm.max_steps
        self.startup_output_budget = vm.output_budget
        super().__init__(vm, **budgets)
        from .bootstrap import normalized
        header = dict(format='pixellang-event-journal', version=3, configuration=deepcopy(self.configuration),
                      program=hashlib.sha256(normalized(vm.bytecode).encode()).hexdigest(),
                      interval_ns=vm.event_binding.interval_ns,
                      environment=digest(dict(input=vm.input_text, arguments=list(vm.arguments))),
                      budgets=dict(step=self.step_budget, result=self.max_result_bytes,
                                   pending=self.max_pending, pending_bytes=self.max_pending_bytes,
                                   heap_items=vm.max_heap_items, heap_objects=vm.max_heap_objects,
                                   depth=vm.max_depth, startup=self.startup_budget,
                                   output=self.startup_output_budget))
        self.journal = EventJournal(path, header, max_bytes=max_journal_bytes,
                                    max_command_bytes=self.max_result_bytes + self.max_pending_bytes + 4096,
                                    max_checkpoint_bytes=max_checkpoint_bytes)
        self.recovering = True
        self.failed = False
        try:
            cursor = self._checkpoint_prefix(self)
            for command, _ in self.journal.scan(cursor):
                self._replay(command)
        except BaseException:
            self.close()
            raise
        finally:
            self.recovering = False

    def close(self):
        self.inspection_cache = None
        self.journal.close()

    def live(self):
        if self.failed or self.journal.closed:
            raise PixelError('event', 'Journal session is closed; reopen to recover committed state')

    def _replay(self, command):
        replay_command(self, command)

    def _checkpoint_prefix(self, runtime, index=None):
        checkpoint = self.journal.checkpoint(index)
        if checkpoint is None:
            return JournalCursor(0, self.journal.seed, 0)
        stream = self.journal.scan()
        try:
            for command, cursor in stream:
                if cursor.revision == checkpoint[1].revision:
                    if cursor != checkpoint[1]:
                        raise PixelError('event', 'Checkpoint prefix accounting is invalid')
                    from .application_checkpoint import restore
                    restore(runtime, checkpoint[0])
                    if encoded(command) != encoded(step_command(runtime.latest, runtime.completed)):
                        raise PixelError('event', 'Checkpoint output does not match its committed event')
                    return cursor
        finally:
            stream.close()
        raise PixelError('event', 'Checkpoint prefix is missing')

    def inspect(self, index):
        """Reconstruct a boundary using a bounded, isolated prefix cache."""
        self.live()
        if type(index) is not int or not 0 <= index <= self.completed:
            raise PixelError('event', 'History index is outside the journal range')
        inspected = self.fresh_runtime()
        cursor = JournalCursor(0, self.journal.seed, 0)
        cached = self.inspection_cache
        if cached is not None and cached['scheduler']['completed'] <= index:
            inspected.vm.restore_checkpoint(cached['vm'])
            for name, value in deepcopy(cached['scheduler']).items():
                setattr(inspected, name, value)
            cursor = cached['cursor']
        else:
            cursor = self._checkpoint_prefix(inspected, index)
        if inspected.completed < index:
            stream = self.journal.scan(cursor)
            try:
                for command, cursor in stream:
                    replay_command(inspected, command)
                    if inspected.completed == index:
                        break
            finally:
                stream.close()
        if inspected.completed != index:
            raise PixelError('event', 'Selected journal event is missing')
        snapshot = dict(vm=inspected.vm.checkpoint(), cursor=cursor,
            scheduler=deepcopy({name: getattr(inspected, name) for name in
                ('completed', 'latest', 'tick', 'sequence', 'pending', 'pending_bytes')}))
        # Publish the cache only after successful replay. Large states fall back
        # to streaming replay; cache pressure never stops application execution.
        self.inspection_cache = snapshot if footprint(snapshot) <= self.max_inspection_bytes else None
        return inspected

    def fresh_runtime(self, bytecode=None):
        from .vm import VM
        vm = self.vm
        return NativeApplicationRuntime(VM(vm.code_image if bytecode is None else bytecode, max_steps=self.startup_budget,
            max_heap_items=vm.max_heap_items, max_heap_objects=vm.max_heap_objects,
            max_depth=vm.max_depth, max_output_chars=self.startup_output_budget,
            input_text=vm.input_text, arguments=list(vm.arguments)),
            configuration=self.configuration, step_budget=self.step_budget, max_result_bytes=self.max_result_bytes,
            max_pending=self.max_pending, max_pending_bytes=self.max_pending_bytes)

    def before_event(self, index, bytecode=None):
        """Replay through input acceptance, stopping before a selected event."""
        self.live()
        if type(index) is not int or not 1 <= index <= self.completed:
            raise PixelError('event', 'Select a completed journal event to debug')
        replay = self.fresh_runtime(bytecode)
        cursor = self._checkpoint_prefix(replay, index - 1)
        for command, _ in self.journal.scan(cursor):
            if command['operation'] == 'step' and command['completed'] == index:
                return replay, command
            replay_command(replay, command)
        raise PixelError('event', 'Selected journal event is missing')

    def history(self, start=0, limit=64):
        """Page event descriptors from disk; no full in-memory history exists."""
        self.live()
        if type(start) is not int or start < 0 or type(limit) is not int or not 1 <= limit <= 128:
            raise PixelError('event', 'History page requires a nonnegative start and limit 1..128')
        rows = self.journal.connection.execute(
            'SELECT event_index, payload FROM commands WHERE event_index>? AND event_index<=? '
            'ORDER BY event_index LIMIT ?', (start, self.completed, limit))
        result = []
        for index, payload in rows:
            command = json.loads(payload)
            result.append(dict(index=index, tick=str(command['tick']), kind=command['kind']))
        return result

    def _accept(self, record):
        if not self.recovering:
            checkpoint = None
            if (self.completed + 1) % self.checkpoint_interval == 0:
                from .application_checkpoint import capture
                from .native_state import NativeStateLimit
                try:
                    checkpoint = capture(self, record, self.journal.max_checkpoint_bytes)
                except NativeStateLimit:
                    pass  # Checkpoint pressure cannot reject a valid event.
            self.journal.append(step_command(record, self.completed + 1), checkpoint)

    def enqueue(self, value, *, tick=None):
        before = (list(self.pending), self.pending_bytes, self.sequence)
        sequence = super().enqueue(value, tick=tick)
        if not self.recovering:
            try:
                item = next(item for item in self.pending if item[1] == sequence)
                self.journal.append(dict(operation='enqueue', tick=item[0], sequence=sequence,
                                         input=self.input_value(item[2])))
            except BaseException:
                self.pending, self.pending_bytes, self.sequence = before
                raise
        return sequence

    def discard(self, sequence):
        before = (list(self.pending), self.pending_bytes)
        super().discard(sequence)
        if not self.recovering:
            try:
                self.journal.append(dict(operation='discard', sequence=sequence))
            except BaseException:
                self.pending, self.pending_bytes = before
                raise

    def step(self):
        revision = self.journal.revision
        try:
            return super().step()
        except BaseException:
            if self.journal.revision != revision:
                self.failed = True
                self.close()
            raise
