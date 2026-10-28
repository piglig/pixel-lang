"""Source/PNG application adapter over the native typed flow event driver."""
import json
from pathlib import Path

from .model import PixelError
from .native_events import NativeApplicationRuntime, NativeEventRuntime
from .vm import VM


class ApplicationSession:
    def __init__(self, compiler, path=None, *, files=None, entry='main.pxl', cancelled=lambda: False,
                 history=None, journal_path=None, configuration=None):
        if journal_path is not None and history is True:
            raise PixelError('event', 'Choose durable journaling or in-memory history')
        history = journal_path is None if history is None else history
        self.compiler = compiler
        self.compiled = None
        self.inspection = None
        if files is not None:
            self.files, self.entry = files, entry
            self.code = compiler.request('compile', files=files, entry=entry, cancelled=cancelled)['result']['bytecode']
        else:
            path = Path(path)
            if path.suffix.lower() == '.png':
                image = path.read_bytes()
                self.code = compiler.request('compile-png', image=image, cancelled=cancelled)['result']['bytecode']
                recovered = compiler.request('recover-png', image=image, cancelled=cancelled)['result']
                self.files, self.entry = recovered['files'], recovered['entry']
            else:
                self.files, self.entry = compiler.read_project(path)
                self.code = compiler.request('compile', files=self.files, entry=self.entry, cancelled=cancelled)['result']['bytecode']
        runtime_type = NativeEventRuntime if history else NativeApplicationRuntime
        if journal_path is not None:
            from .event_journal import JournaledApplicationRuntime
            self.runtime = JournaledApplicationRuntime(VM(self.code, max_steps=1_000_000), journal_path,
                                                       configuration=configuration)
        else:
            self.runtime = runtime_type(VM(self.code, max_steps=1_000_000), configuration=configuration)

    def close(self):
        close = getattr(self.runtime, 'close', None)
        if close is not None:
            close()

    def status(self, include_source=False):
        runtime = self.inspection or self.runtime
        binding = runtime.vm.event_binding
        recording = isinstance(runtime, NativeEventRuntime)
        journal = getattr(self.runtime, 'journal', None)
        cursor = runtime.cursor if recording else runtime.completed
        start = max(0, cursor - 64)
        selected = (runtime.records[cursor - 1] if cursor else None) if recording else runtime.latest
        cells = []
        for identity, instance in runtime.vm.flow_runtime.instances.items():
            for (path, node), value in instance.cells.items():
                cells.append(dict(instance=identity, path=list(path), node=node,
                                  value=runtime.input_value(value)))
        return dict(cursor=cursor, length=len(runtime.records) if recording else self.runtime.completed,
            history=recording or journal is not None,
            journal=str(journal.path) if journal is not None else None,
            tick=str(selected.tick) if selected else None,
            elapsed_ns=str(selected.tick * binding.interval_ns) if selected else '0',
            interval_ns=str(binding.interval_ns),
            timed=binding.clock_type is not None,
            view=runtime.view(), viewText=json.dumps(runtime.view(), ensure_ascii=False, indent=2),
            stateText=json.dumps(cells, ensure_ascii=False, indent=2),
            eventType=binding.event_type, variants=self.code['enums'][binding.event_type],
            records=self.code['records'], enums=self.code['enums'],
            pending=[dict(tick=str(tick), sequence=sequence, valueText=json.dumps(runtime.input_value(event), ensure_ascii=False))
                     for tick, sequence, event, _ in sorted(self.runtime.pending)],
            events=[dict(index=start+i+1, tick=str(record.tick), kind=record.kind)
                    for i, record in enumerate(runtime.records[start:start+128])] if recording else
                    self.runtime.history(start, 128) if journal is not None else [],
            **({'source_project': dict(files=self.files, entry=self.entry)} if include_source else {}))

    def request(self, method, args):
        runtime = self.runtime
        if self.inspection is not None and method in ('application-step', 'application-input', 'application-discard'):
            raise PixelError('event', 'Return to the latest event before advancing or sending input')
        if method == 'application-seek' and hasattr(runtime, 'journal'):
            index = args['index']
            if type(index) is not int or not 0 <= index <= runtime.completed:
                raise PixelError('event', 'History index is outside the journal range')
            self.inspection = None if index == runtime.completed else runtime.inspect(index)
            return self.status()
        if (method in ('application-seek', 'application-recording', 'application-restore')
                and not isinstance(runtime, NativeEventRuntime)):
            raise PixelError('event', 'History was not enabled for this application session')
        if method == 'application-step':
            runtime.step()
        elif method == 'application-input':
            # Preserve signed 64-bit fields across JavaScript's number boundary.
            value = json.loads(args['valueText'])
            tick = args.get('tick')
            if tick is not None:
                if not isinstance(tick, str) or not tick.isdecimal():
                    raise PixelError('event', 'Tick must be a nonnegative integer string')
                tick = int(tick)
            runtime.enqueue(value, tick=tick)
        elif method == 'application-discard':
            runtime.discard(args['sequence'])
        elif method == 'application-seek':
            runtime.seek(args['index'])
        elif method == 'application-recording':
            return dict(recordingText=json.dumps(runtime.recording(), ensure_ascii=False, indent=2))
        elif method == 'application-restore':
            text = args['recordingText']
            if not isinstance(text, str) or len(text.encode()) > 64_000_000:
                raise PixelError('event', 'Recording exceeds 64 MB')
            restored = NativeEventRuntime.restore(VM(self.code, max_steps=1_000_000), json.loads(text))
            self.runtime = restored
        elif method != 'application-status':
            raise PixelError('event', 'Unknown application operation')
        return self.status()

    def debug_event(self, index):
        from .application_debug import EventTimeline
        if not isinstance(self.runtime, NativeEventRuntime) and not hasattr(self.runtime, 'journal'):
            raise PixelError('event', 'History was not enabled for this application session')
        if self.compiled is None:
            self.compiled = self.compiler.debug_project(self.files, self.entry)
        return EventTimeline(self.compiled, self.runtime, index)
