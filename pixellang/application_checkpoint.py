"""Typed event-boundary checkpoints; execution frames are recreated by startup."""
import heapq
import json

from .native_events import EventRecord
from .native_state import NativeStateCodec, cell_types, integer, invalid


def capture(runtime, record, max_bytes=64_000_000):
    vm = runtime.vm
    root = vm.flow_runtime.instances[vm.event_binding.handle.identity]
    codec = NativeStateCodec(vm, max_bytes=max_bytes)
    pending = sorted(runtime.pending)
    if record.kind == 'input':
        if not pending or pending[0][1] != record.sequence:
            invalid('pending input does not match event')
        pending = pending[1:]
    return dict(version=2, completed=runtime.completed + 1,
        tick=record.tick, sequence=runtime.sequence + (record.kind == 'tick'),
        vm_steps=vm.steps, instance_steps=root.steps,
        cells=[dict(path=list(path), node=node, value=codec.encode(value))
               for (path, node), value in sorted(root.cells.items())],
        configuration=[dict(path=list(path), values=[codec.encode(value) for value in values])
                       for path, values in sorted(root.configuration.items())],
        pending=[dict(tick=tick, sequence=sequence, value=codec.encode(value), size=size)
                 for tick, sequence, value, size in pending],
        latest=dict(tick=record.tick, delta_ns=record.delta_ns, sequence=record.sequence,
                    kind=record.kind, event=codec.encode(record.event), output=codec.encode(record.output), size=record.size))


def restore(runtime, document):
    """Apply validated state to a freshly registered application, never a PC/heap."""
    if runtime.completed or runtime.latest is not None or runtime.pending:
        invalid('checkpoint requires a fresh application')
    names = {'version','completed','tick','sequence','vm_steps','instance_steps','cells','configuration','pending','latest'}
    if (not isinstance(document, dict) or set(document) != names
            or type(document['version']) is not int or document['version'] != 2):
        invalid('checkpoint fields do not match')
    completed = integer(document['completed'], 1)
    tick = integer(document['tick'])
    sequence = integer(document['sequence'], 1)
    if tick >= completed or sequence < tick + 1: invalid('invalid scheduler counters')
    vm = runtime.vm
    binding = vm.event_binding
    if tick * binding.interval_ns >= (1 << 63): invalid('logical time overflow')
    root = vm.flow_runtime.instances[binding.handle.identity]
    steps = integer(document['instance_steps'])
    if steps != root.steps + completed: invalid('instance step count does not match')
    vm_steps = integer(document['vm_steps'])
    if vm_steps < vm.steps: invalid('instruction count precedes startup')
    codec = NativeStateCodec(vm)
    expected = cell_types(vm)
    cells = {}
    if not isinstance(document['cells'], list) or len(document['cells']) != len(expected):
        invalid('state cells do not match the flow')
    for cell in document['cells']:
        if not isinstance(cell, dict) or set(cell) != {'path','node','value'} or not isinstance(cell['path'], list):
            invalid('invalid state cell')
        key = (tuple(integer(n) for n in cell['path']), integer(cell['node']))
        if key not in expected or key in cells: invalid('unknown or repeated state cell')
        value = codec.decode(cell['value'])
        codec.typed(value, expected[key])
        cells[key] = value
    configuration = {}
    raw_configuration = document['configuration']
    if not isinstance(raw_configuration, list) or len(raw_configuration) != len(root.configuration):
        invalid('configuration paths do not match the flow')
    for item in raw_configuration:
        if not isinstance(item, dict) or set(item) != {'path', 'values'} or not isinstance(item['path'], list):
            invalid('invalid configuration input')
        path = tuple(integer(n) for n in item['path'])
        if path not in root.configuration or path in configuration:
            invalid('unknown or repeated configuration path')
        graph = vm.bytecode['flows'][root.handle.target]
        for node in path:
            target = next(child['function'] for child in graph['children'] if child['node'] == node)
            graph = vm.bytecode['flows'][target]
        if not isinstance(item['values'], list) or len(item['values']) != len(graph['initialInputs']):
            invalid('configuration input count mismatch')
        values = []
        for encoded, parameter in zip(item['values'], graph['initialInputs']):
            value = codec.decode(encoded)
            codec.typed(value, parameter['type'])
            values.append(value)
        configuration[path] = values
    raw_pending = document['pending']
    if not isinstance(raw_pending, list) or len(raw_pending) > runtime.max_pending: invalid('pending queue budget exceeded')
    pending, seen, pending_bytes = [], set(), 0
    for item in raw_pending:
        if not isinstance(item, dict) or set(item) != {'tick','sequence','value','size'}: invalid('invalid pending input')
        when, order, size = integer(item['tick']), integer(item['sequence']), integer(item['size'], 1)
        if when < tick or order >= sequence or order in seen: invalid('invalid pending input order')
        seen.add(order)
        value = codec.decode(item['value'])
        codec.typed(value, binding.event_type)
        pending.append((when, order, value, size))
        pending_bytes += size
    if pending_bytes > runtime.max_pending_bytes: invalid('pending queue budget exceeded')
    latest = document['latest']
    if not isinstance(latest, dict) or set(latest) != {'tick','delta_ns','sequence','kind','event','output','size'}:
        invalid('invalid last event')
    if binding.clock_type is None and (tick != 0 or latest['kind'] != 'input'):
        invalid('event-only checkpoint contains timer progress')
    if latest['kind'] not in ('tick','input') or integer(latest['tick']) != tick: invalid('invalid last event time')
    delta = integer(latest['delta_ns'])
    if delta != (binding.interval_ns if latest['kind'] == 'tick' and tick else 0): invalid('invalid event delta')
    order = integer(latest['sequence'])
    if order >= sequence or order in seen: invalid('invalid last event sequence')
    event, output = codec.decode(latest['event']), codec.decode(latest['output'])
    codec.typed(event, binding.event_type)
    codec.typed(output, binding.result_type)
    if latest['kind'] == 'tick' and event != binding.tick_event: invalid('tick value does not match registration')
    display = runtime.input_value(output)
    if len(json.dumps(display, ensure_ascii=False, allow_nan=False).encode()) > runtime.max_result_bytes:
        invalid('output byte budget exceeded')
    record = EventRecord(tick, delta, order, latest['kind'], event, output, display, integer(latest['size'], 1))
    # No mutation before the complete document has passed validation.
    before = vm.checkpoint()
    root = vm.flow_runtime.instances[binding.handle.identity]
    try:
        root.configuration = configuration
        root.cells = cells
        root.steps = steps
        vm.steps = vm_steps
        vm.recount_spatial()
    except BaseException:
        vm.restore_checkpoint(before)
        raise
    runtime.completed, runtime.tick, runtime.sequence = completed, tick, sequence
    heapq.heapify(pending)
    runtime.pending, runtime.pending_bytes, runtime.latest = pending, pending_bytes, record
