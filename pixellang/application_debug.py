"""Instruction inspection of one native application event in an isolated VM."""
from .model import PixelError
from .native_events import NativeApplicationRuntime
from .temporal import Timeline
from .vm import VM


class EventDebugVM(VM):
    def step(self, snapshot=True):
        result = super().step(snapshot=snapshot)
        binding = self.event_binding
        if binding and not self.halted and len(self.call_stack) <= binding.depth:
            # This debug execution finishes at the selected event boundary.
            self.halted = True
        return self.state() if snapshot else result


class EventTimeline(Timeline):
    def __init__(self, compiled, application, index):
        if hasattr(application, 'journal'):
            replay, command = application.before_event(index, compiled.bytecode)
            tick = command['tick']
            if command['kind'] == 'input':
                event = replay.pending[0][2]
                delta = 0
            else:
                event = replay.vm.event_binding.tick_event
                delta = replay.vm.event_binding.interval_ns if tick else 0
        else:
            if type(index) is not int or not 1 <= index <= len(application.records):
                raise PixelError('event', 'Select a completed event to debug')
            # Replay from the frozen authoring snapshot with generated source maps.
            # No live VM state, pending queue, or historical cursor is mutated.
            replay = NativeApplicationRuntime(VM(compiled.bytecode, max_steps=1_000_000),
                                              configuration=application.configuration)
            for record in application.records[:index-1]:
                if record.kind == 'input':
                    replay.enqueue(application.input_value(record.event), tick=record.tick)
                if replay.step() != record.display:
                    raise PixelError('event', 'Debug source replay differs from the application')
            record = application.records[index-1]
            if record.kind == 'input':
                replay.enqueue(application.input_value(record.event), tick=record.tick)
                event = replay.pending[0][2]
            else:
                event = replay.vm.event_binding.tick_event
            tick, delta = record.tick, record.delta_ns
        replay.begin_event(event, tick, delta)
        self.initial_event = replay.vm.checkpoint()
        self.event_step_limit = replay.vm.steps + replay.step_budget
        super().__init__(compiled.source, compiled=compiled, max_steps=replay.step_budget)

    def fresh_vm(self, live=False):
        vm = EventDebugVM(self.code_image, max_steps=self.event_step_limit,
                          file_access=self.file_access if live else self.file_access.replay())
        vm.restore_checkpoint(self.initial_event)
        return vm

    def document(self):
        raise PixelError('event', 'Reopen the application recording or journal to reconstruct and debug its events')
