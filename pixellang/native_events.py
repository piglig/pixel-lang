"""Typed application bindings for the native flow event driver.

The host selects an initialized flow instance and supplies its clock policy.
Flow creation and stepping do not depend on the application protocol.
"""
from dataclasses import dataclass
from copy import deepcopy
import hashlib
import heapq
import json

from .typeexpr import parse_type
from . import jsonvalue
from .model import PixelError
from .recording import footprint


def application_contract(bytecode, signature):
    """Resolve the event input and optional structural clock protocol.

    Record identities stay nominal. Field requirements describe the interface at
    this boundary without relying on optional source/module names in a PNG.
    """
    handle = parse_type(signature)
    if handle.kind != 'flow_handle':
        raise ValueError('Application requires a flow instance')
    function = handle.args[0]
    if len(function.args) not in (2, 3):
        raise ValueError('Application flow requires an event input and an optional clock input')
    event, result = function.args[0].spelling(), function.args[-1].spelling()
    clock = function.args[1].spelling() if len(function.args) == 3 else None
    if event not in bytecode['enums']:
        raise ValueError('Application event must be an enum')
    if clock is None:
        return event, None, None, result
    fields = bytecode['records'].get(clock, {})
    if set(fields) != {'index', 'elapsed', 'delta'} or fields['index'] != 'int':
        raise ValueError('Application clock requires index, elapsed and delta fields')
    duration = fields['elapsed']
    if fields['delta'] != duration or bytecode['records'].get(duration) != {'nanoseconds': 'int'}:
        raise ValueError('Application clock requires matching nanosecond duration records')
    return event, clock, duration, result


@dataclass(frozen=True)
class EventBinding:
    handle: object
    event_type: str
    clock_type: str | None
    duration_type: str | None
    result_type: str
    tick_event: object
    interval_ns: int
    depth: int


def configure(vm, configuration=None):
    """Attach host scheduling after normal program initialization has finished."""
    configuration = {} if configuration is None else configuration
    if not isinstance(configuration, dict) or set(configuration) - {'instance', 'tick', 'intervalNanoseconds'}:
        raise PixelError('event', 'Invalid application configuration')
    vm.flow_runtime.idle(vm, None)
    if vm.call_stack or not vm.halted or vm.event_binding is not None:
        raise PixelError('event', 'Application configuration requires completed initialization')
    instances = vm.flow_runtime.instances
    identity = configuration.get('instance')
    if identity is None:
        if len(instances) != 1:
            raise PixelError('event', 'Application requires exactly one initialized flow, or an explicit instance selection')
        identity = next(iter(instances))
    if type(identity) is not int or identity not in instances:
        raise PixelError('event', 'Unknown application flow instance')
    handle = instances[identity].handle
    signature = 'Flow<' + handle.signature + '>'
    try:
        event_type, clock_type, duration_type, result_type = application_contract(vm.bytecode, signature)
    except (ValueError, IndexError, KeyError) as error:
        raise PixelError('event', str(error)) from error
    if clock_type is None:
        if set(configuration) & {'tick', 'intervalNanoseconds'}:
            raise PixelError('event', 'Event-only applications do not accept timer configuration')
        vm.event_binding = EventBinding(handle, event_type, None, None, result_type, None, 0, 0)
        vm.halted = False
        vm.event_waiting = True
        return dict(instance=identity)
    interval_ns = configuration.get('intervalNanoseconds', 100_000_000)
    if type(interval_ns) is not int or not 0 < interval_ns < (1 << 63):
        raise PixelError('event', 'Application interval must be positive nanoseconds')
    tick = configuration.get('tick', {'variant': 'Tick', 'fields': {}})
    event = vm.decode_json(jsonvalue.from_host(tick), event_type, None)
    frozen_tick = vm.freeze_spatial_payload(event, None)
    vm.event_binding = EventBinding(handle, event_type, clock_type, duration_type,
                                   result_type, frozen_tick, interval_ns, 0)
    vm.halted = False
    vm.event_waiting = True
    return dict(instance=identity, tick=deepcopy(tick), intervalNanoseconds=interval_ns)


@dataclass(frozen=True)
class EventRecord:
    tick: int
    delta_ns: int
    sequence: int
    kind: str
    event: object
    output: object
    display: object
    size: int


class NativeApplicationRuntime:
    """One deterministic queue driving a registered native flow instance.

    Single-input applications consume only explicitly queued business events.
    For clocked applications, tick zero establishes the initial view. Each later tick advances by exactly
    the registered interval. Queued inputs at a tick follow that tick event, in
    enqueue order, with zero delta. Playback speed never enters this class.
    """
    def __init__(self, vm, *, configuration=None, step_budget=1_000_000, max_result_bytes=64_000_000,
                 max_pending=1024, max_pending_bytes=64_000_000):
        for value in (step_budget, max_result_bytes, max_pending, max_pending_bytes):
            if type(value) is not int or value <= 0:
                raise ValueError('Event budgets must be positive integers')
        self.vm = vm
        self.step_budget = step_budget
        self.max_result_bytes = max_result_bytes
        self.max_pending = max_pending
        self.max_pending_bytes = max_pending_bytes
        while not vm.halted:
            vm.step(snapshot=False)
        self.configuration = configure(vm, configuration)
        self.completed = 0
        self.latest = None
        self.tick = -1
        self.sequence = 0
        self.pending = []
        self.pending_bytes = 0

    def live(self):
        """Execution without a recorder always observes the live frontier."""

    def _accept(self, record):
        """Validate and retain an event before publishing scheduler progress."""

    def enqueue(self, value, *, tick=None):
        self.live()
        tick = max(self.tick, 0) if tick is None else tick
        if type(tick) is not int or not max(self.tick, 0) <= tick < (1 << 63):
            raise PixelError('event', 'Input tick must not precede the current logical time')
        if self.vm.event_binding.clock_type is None and tick != 0:
            raise PixelError('event', 'Event-only applications do not schedule inputs by time')
        tagged = jsonvalue.from_host(value)
        size = len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode())
        if len(self.pending) >= self.max_pending or self.pending_bytes + size > self.max_pending_bytes:
            raise PixelError('event', 'Pending event budget exceeded')
        # Decoding validates the exact enum and payload types. Do not retain
        # temporary VM allocations or consume simulation budgets on UI input.
        checkpoint = self.vm.checkpoint()
        try:
            event = self.vm.decode_json(tagged, self.vm.event_binding.event_type, None)
            frozen = self.vm.freeze_spatial_payload(event, None)
        finally:
            self.vm.restore_checkpoint(checkpoint)
        sequence = self.sequence
        heapq.heappush(self.pending, (tick, sequence, frozen, size))
        self.pending_bytes += size
        self.sequence += 1
        return sequence

    def begin_event(self, event, tick, delta_ns):
        """Prepare native arguments and enter the flow; used by execution and debugging."""
        vm = self.vm
        binding = vm.event_binding
        elapsed_ns = tick * binding.interval_ns
        if not 0 <= elapsed_ns < (1 << 63):
            raise PixelError('event', 'Logical time exceeds signed 64-bit nanoseconds')
        vm.event_waiting = False
        base = len(vm.stack)
        argument = vm.thaw_spatial_payload(event, None)
        vm.stack.append(argument)
        arguments = [argument]
        if binding.clock_type is not None:
            elapsed = vm.allocate(binding.duration_type, {'nanoseconds': elapsed_ns}, None)
            vm.stack.append(elapsed)
            delta = vm.allocate(binding.duration_type, {'nanoseconds': delta_ns}, None)
            vm.stack.append(delta)
            clock = vm.allocate(binding.clock_type, dict(index=tick, elapsed=elapsed, delta=delta), None)
            vm.stack.append(clock)
            arguments.append(clock)
        vm.flow_runtime.advance(vm, binding.handle, arguments, None)
        vm.call_stack[-1].base = base
        vm.call_stack[-1].return_to_host = True
        del vm.stack[base:]
        return base

    def _invoke(self, event, tick, delta_ns, *, checkpoint=None):
        vm = self.vm
        binding = vm.event_binding
        elapsed_ns = tick * binding.interval_ns
        if not 0 <= elapsed_ns < (1 << 63):
            raise PixelError('event', 'Logical time exceeds signed 64-bit nanoseconds')
        checkpoint = vm.checkpoint() if checkpoint is None else checkpoint
        limit = vm.max_steps
        boundary = [(frame.function, frame.pc) for frame in vm.call_stack]
        instance_steps = vm.flow_runtime.instances[binding.handle.identity].steps
        try:
            vm.max_steps = vm.steps + self.step_budget
            vm.event_waiting = False
            base = self.begin_event(event, tick, delta_ns)
            while len(vm.call_stack) > binding.depth and not vm.halted:
                vm.step(snapshot=False)
            if (vm.halted or len(vm.call_stack) != binding.depth or len(vm.stack) != base + 1
                    or [(frame.function, frame.pc) for frame in vm.call_stack] != boundary
                    or vm.flow_runtime.instances[binding.handle.identity].steps != instance_steps + 1):
                raise PixelError('event', 'Application did not return to its event boundary')
            result = vm.stack[-1]
            if binding.result_type != 'unit':
                vm.typed(result, binding.result_type, None)
            frozen = vm.freeze_spatial_payload(result, None)
            output_budget = vm.output_budget
            try:
                vm.output_budget = self.max_result_bytes
                display = vm.materialize(result)
            finally:
                vm.output_budget = output_budget
            size = len(json.dumps(display, ensure_ascii=False, allow_nan=False).encode())
            if size > self.max_result_bytes:
                raise PixelError('event', 'Event result exceeds the result byte budget')
            vm.stack.pop()
            vm.event_waiting = True
            vm.recount_spatial()
            return frozen, display, size
        except BaseException:
            vm.restore_checkpoint(checkpoint)
            raise
        finally:
            vm.max_steps = limit

    def step(self):
        self.live()
        binding = self.vm.event_binding
        event_only = binding.clock_type is None
        if event_only and not self.pending:
            raise PixelError('event', 'No pending application event')
        queued = bool(self.pending and (event_only or self.pending[0][0] <= self.tick))
        if queued:
            tick, sequence, event, input_size = self.pending[0]
            delta, kind = 0, 'input'
        else:
            tick, sequence, event, input_size = self.tick + 1, self.sequence, binding.tick_event, 0
            delta, kind = (0 if tick == 0 else binding.interval_ns), 'tick'
        checkpoint = self.vm.checkpoint()
        frozen, display, size = self._invoke(event, tick, delta, checkpoint=checkpoint)
        record = EventRecord(tick, delta, sequence, kind, event, frozen, display, size + input_size + 64)
        try:
            self._accept(record)
        except BaseException:
            self.vm.restore_checkpoint(checkpoint)
            raise
        if queued:
            heapq.heappop(self.pending)
            self.pending_bytes -= input_size
            if event_only:
                self.tick = 0
        else:
            self.tick = tick
            self.sequence += 1
        self.completed += 1
        self.latest = record
        return deepcopy(display)

    def view(self):
        return deepcopy(self.latest.display) if self.latest is not None else None

    def discard(self, sequence):
        """Explicitly remove a rejected or unwanted pending input."""
        self.live()
        if type(sequence) is not int:
            raise PixelError('event', 'Input sequence must be an integer')
        item = next((item for item in self.pending if item[1] == sequence), None)
        if item is None:
            raise PixelError('event', 'Pending input does not exist')
        self.pending.remove(item)
        heapq.heapify(self.pending)
        self.pending_bytes -= item[3]

    def input_value(self, frozen):
        checkpoint = self.vm.checkpoint()
        try:
            self.vm.output_budget = self.max_result_bytes
            return self.vm.materialize(self.vm.thaw_spatial_payload(frozen, None))
        finally:
            self.vm.restore_checkpoint(checkpoint)

class NativeEventRuntime(NativeApplicationRuntime):
    """Bounded recording and seek layered over the native application scheduler.

    History budgets belong to this recorder, not to application execution.
    """
    def __init__(self, vm, *, configuration=None, step_budget=1_000_000, max_events=4096,
                 max_history_bytes=64_000_000, max_pending=1024, max_checkpoint_bytes=64_000_000):
        for value in (max_events, max_history_bytes, max_checkpoint_bytes):
            if type(value) is not int or value <= 0:
                raise ValueError('Event budgets must be positive integers')
        super().__init__(vm, configuration=configuration, step_budget=step_budget, max_result_bytes=max_history_bytes,
                         max_pending=max_pending, max_pending_bytes=max_history_bytes)
        self.max_events = max_events
        self.max_history_bytes = max_history_bytes
        self.max_checkpoint_bytes = max_checkpoint_bytes
        self.records = []
        self.cursor = 0
        self.history_bytes = 0
        self.checkpoints = {0: vm.checkpoint()}
        self.checkpoint_sizes = {0: footprint(self.checkpoints[0])}
        self.checkpoint_bytes = self.checkpoint_sizes[0]
        if self.checkpoint_bytes > max_checkpoint_bytes:
            raise PixelError('event', 'Initial application state exceeds the checkpoint budget')

    def live(self):
        if self.cursor != len(self.records):
            raise PixelError('event', 'Return to the latest event before advancing or sending input')

    def step(self):
        self.live()
        if len(self.records) >= self.max_events:
            raise PixelError('event', 'Event history limit reached')
        return super().step()

    def _accept(self, record):
        if self.history_bytes + record.size > self.max_history_bytes:
            raise PixelError('event', 'Event history byte budget exceeded')
        cursor = len(self.records) + 1
        # Prepare any fallible checkpoint work before mutating the recorder.
        checkpoint = self.vm.checkpoint() if cursor % 64 == 0 else None
        size = footprint(checkpoint) if checkpoint is not None else 0
        if checkpoint is not None and size + self.checkpoint_sizes[0] <= self.max_checkpoint_bytes:
            while self.checkpoint_bytes + size > self.max_checkpoint_bytes:
                victim = min(key for key in self.checkpoints if key != 0)
                del self.checkpoints[victim]
                self.checkpoint_bytes -= self.checkpoint_sizes.pop(victim)
            self.checkpoints[cursor] = checkpoint
            self.checkpoint_sizes[cursor] = size
            self.checkpoint_bytes += size
        self.records.append(record)
        self.history_bytes += record.size
        self.cursor = cursor

    def seek(self, index):
        if type(index) is not int or not 0 <= index <= len(self.records):
            raise PixelError('event', 'History index is outside the recorded range')
        previous = self.vm.checkpoint()
        start = max(key for key in self.checkpoints if key <= index)
        try:
            self.vm.restore_checkpoint(self.checkpoints[start])
            for record in self.records[start:index]:
                output, _, _ = self._invoke(record.event, record.tick, record.delta_ns)
                if output != record.output:
                    raise PixelError('event', 'Native event replay diverged')
        except BaseException:
            self.vm.restore_checkpoint(previous)
            raise
        self.cursor = index
        return self.view()

    def view(self):
        return deepcopy(self.records[self.cursor - 1].display) if self.cursor else None

    def program_digest(self):
        from .bootstrap import normalized
        return hashlib.sha256(normalized(self.vm.bytecode).encode()).hexdigest()

    def recording(self):
        return dict(format='pixellang-native-events', version=2, configuration=deepcopy(self.configuration),
            program=self.program_digest(), interval_ns=self.vm.event_binding.interval_ns,
            cursor=self.cursor,
            events=[dict(tick=r.tick, kind=r.kind,
                         input=self.input_value(r.event) if r.kind == 'input' else None,
                         output=deepcopy(r.display)) for r in self.records],
            pending=[dict(tick=tick, input=self.input_value(event))
                     for tick, _, event, _ in sorted(self.pending)])

    @classmethod
    def restore(cls, vm, document, **budgets):
        """Re-execute typed inputs; never trust a serialized state snapshot."""
        if not isinstance(document, dict) or not isinstance(document.get('configuration'), dict):
            raise PixelError('event', 'Recording requires application configuration')
        configuration = document['configuration']
        runtime = cls(vm, configuration=configuration, **budgets)
        if (not isinstance(document, dict) or document.get('format') != 'pixellang-native-events'
                or document.get('version') != 2 or document.get('program') != runtime.program_digest()
                or configuration != runtime.configuration
                or document.get('interval_ns') != vm.event_binding.interval_ns):
            raise PixelError('event', 'Recording does not match this native application')
        events, pending = document.get('events'), document.get('pending')
        if (not isinstance(events, list) or len(events) > runtime.max_events
                or not isinstance(pending, list) or len(pending) > runtime.max_pending):
            raise PixelError('event', 'Invalid recording event counts')
        for record in events:
            if not isinstance(record, dict) or record.get('kind') not in ('tick', 'input'):
                raise PixelError('event', 'Invalid recording event')
            tick = record.get('tick')
            if vm.event_binding.clock_type is None and record['kind'] != 'input':
                raise PixelError('event', 'Event-only recordings cannot contain timer events')
            expected = 0 if vm.event_binding.clock_type is None else runtime.tick + (record['kind'] == 'tick')
            if type(tick) is not int or tick != expected or tick < 0:
                raise PixelError('event', 'Recording has invalid logical time order')
            if record['kind'] == 'input':
                runtime.enqueue(record.get('input'), tick=tick)
            elif record.get('input') is not None:
                raise PixelError('event', 'Tick events use the registered tick value')
            output = runtime.step()
            if output != record.get('output'):
                raise PixelError('event', 'Native event recording replay diverged')
        for item in pending:
            if not isinstance(item, dict) or 'tick' not in item or 'input' not in item:
                raise PixelError('event', 'Invalid pending recording input')
            runtime.enqueue(item['input'], tick=item['tick'])
        runtime.seek(document.get('cursor'))
        return runtime
