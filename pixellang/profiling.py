"""Opt-in, bounded VM profiling; never changes bytecode or execution semantics."""

import time
import math
from collections import Counter

from .vm import VM

PHASE_PREFIX = '__pixel_profile_phase__:'


class ScopedProfileVM(VM):
    """Exclusive phase timing at VM call/return boundaries; no source markers.

    Unmapped helpers inherit their nearest mapped caller. Times include profiler
    overhead and GC; gcSeconds is a subset, not an additional duration.
    """
    def __init__(self, *args, function_phases=None, **kwargs):
        self.function_phases = function_phases or {}
        self.phase_totals = {}
        self.gc_seconds = 0.0
        self.gc_count = 0
        super().__init__(*args, **kwargs)
        self._profile_frame = self.call_stack[-1] if self.call_stack else None
        self._profile_phase = self._current_phase()
        self._profile_clock = time.perf_counter()
        self._profile_steps = self.steps
        self._profile_gc = self.gc_seconds

    def _current_phase(self):
        for frame in reversed(self.call_stack):
            if frame.function in self.function_phases:
                return self.function_phases[frame.function]
        return 'driver/transport'

    def _account(self):
        now = time.perf_counter()
        row = self.phase_totals.setdefault(self._profile_phase,
            dict(seconds=0.0, steps=0, gcSeconds=0.0))
        row['seconds'] += now - self._profile_clock
        row['steps'] += self.steps - self._profile_steps
        row['gcSeconds'] += self.gc_seconds - self._profile_gc
        self._profile_clock, self._profile_steps = now, self.steps
        self._profile_gc = self.gc_seconds

    def collect(self, extra_roots=()):
        start = time.perf_counter()
        try:
            return super().collect(extra_roots)
        finally:
            self.gc_seconds += time.perf_counter() - start
            self.gc_count += 1

    def step(self, snapshot=True):
        try:
            return super().step(snapshot=snapshot)
        finally:
            frame = self.call_stack[-1] if self.call_stack else None
            if frame is not self._profile_frame:
                phase = self._current_phase()
                if phase != self._profile_phase:
                    self._account()
                    self._profile_phase = phase
                self._profile_frame = frame

    def phase_summary(self):
        self._account()
        return dict(phases={name: dict(row) for name, row in self.phase_totals.items()},
                    steps=self.steps, halted=self.halted,
                    gcSeconds=self.gc_seconds, gcCount=self.gc_count)


class ProfileVM(VM):
    def __init__(self, *args, **kwargs):
        self.gc_seconds = 0.0
        self.gc_count = 0
        super().__init__(*args, **kwargs)

    def collect(self, extra_roots=()):
        start = time.perf_counter()
        try:
            return super().collect(extra_roots)
        finally:
            self.gc_seconds += time.perf_counter() - start
            self.gc_count += 1


def run_profile(vm, *, timeout=60, interval=10000, report=lambda event: None):
    if not math.isfinite(timeout) or timeout <= 0 or type(interval) is not int or interval < 1:
        raise ValueError('Profile timeout and interval must be positive')
    start = phase_start = time.perf_counter()
    phase, phase_steps, previous_output = 'input', vm.steps, len(vm.output)
    phases, samples = [], Counter()
    peak_items, peak_objects = vm.heap_items, len(vm.heap)
    phase_gc = vm.gc_seconds

    def finish_phase():
        event = dict(phase=phase, seconds=time.perf_counter() - phase_start,
                     steps=vm.steps - phase_steps, gcSeconds=vm.gc_seconds - phase_gc)
        phases.append(event)
        report(event)

    try:
        while not vm.halted:
            vm.step(snapshot=False)
            if len(vm.output) != previous_output:
                previous_output = len(vm.output)
                last = vm.output[-1]
                if isinstance(last, str) and last.startswith(PHASE_PREFIX):
                    finish_phase()
                    phase = last[len(PHASE_PREFIX):]
                    phase_start, phase_steps, phase_gc = time.perf_counter(), vm.steps, vm.gc_seconds
            if vm.steps % interval == 0:
                peak_items = max(peak_items, vm.heap_items)
                peak_objects = max(peak_objects, len(vm.heap))
                if vm.call_stack:
                    frame = vm.call_stack[-1]
                    instructions = vm.bytecode['functions'][frame.function]['instructions']
                    span = instructions[min(frame.pc, len(instructions) - 1)].get('span') or {}
                    source = span.get('source', '?')
                    samples[source] += 1
                if time.perf_counter() - start > timeout:
                    raise TimeoutError(f'Profile exceeded {timeout} seconds')
    finally:
        finish_phase()
        report(dict(kind='profile', seconds=time.perf_counter() - start, steps=vm.steps,
                    halted=vm.halted, gcSeconds=vm.gc_seconds, gcCount=vm.gc_count,
                    sampledPeakHeapItems=max(peak_items, vm.heap_items),
                    sampledPeakHeapObjects=max(peak_objects, len(vm.heap)),
                    sampleInterval=interval, sourceSamples=dict(samples.most_common()), phases=phases))
    return [item for item in vm.output if not (isinstance(item, str) and item.startswith(PHASE_PREFIX))]
