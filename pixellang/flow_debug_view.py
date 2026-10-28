"""Paged native graph overlays for one paused VM snapshot; no value materialization."""
from .debugvalues import describe_value
from .flow_debug import flow_values
from .model import PixelError


def graph_view(timeline, args):
    if args.get('z') != timeline.cursor:
        raise PixelError('debug', 'Graph view belongs to a different time')
    vm = timeline.vm if timeline.cursor == len(timeline.events) else timeline.replay_vm
    frame_index = args.get('frame')
    values = flow_values(vm, frame_index)
    frame = vm.call_stack[-frame_index - 1]
    graph = vm.bytecode['flows'][frame.function]
    info = timeline.compiled.debug['functions'][frame.function]
    start, count = args.get('start', 0), args.get('count', 100)
    if type(start) is not int or start < 0 or type(count) is not int or not 1 <= count <= 100:
        raise PixelError('debug', 'Invalid graph page')
    base = ['flow', frame_index]

    def preview(value, path, name):
        return describe_value(timeline, vm, value, base + path, name)

    named = []
    for item in graph['values'][start:start + count]:
        name = item['name']
        state = values['values'][name]
        named.append(dict(node=item['node'], name=name, phase=state['phase'],
                          result=preview(state['result'], ['values', name, 'result'], name)))
    inputs = [dict(node=item['node'], value=preview(values['inputs'][item['name']],
               ['inputs', item['name']], item['name'])) for item in graph['inputs'][start:start + count]]
    states = []
    for item in graph.get('states', [])[start:start + count]:
        name = item['name']
        update = values['nextState'][name]
        states.append(dict(node=item['node'], name=name, staged=update['staged'], retained=update['retained'],
                           old=preview(values['oldState'][name], ['oldState', name], name),
                           next=preview(update['value'], ['nextState', name, 'value'], name)))
    return dict(z=timeline.cursor, source=info['source'], declaration=graph['node'],
                function=frame.function, instance=values['instance'], step=values['step'],
                frame=frame_index, start=start, count=count,
                totals=dict(values=len(graph['values']), children=len(graph['children']), inputs=len(graph['inputs']), states=len(graph['states'])),
                values=named, children=[values['children'][item['name']] for item in graph['children'][start:start + count]],
                inputs=inputs, states=states,
                output=preview(values['output'], ['output'], 'output'))


def frame_view(timeline, args):
    """List active graph owners without serializing bytecode or runtime values."""
    if args.get('z') != timeline.cursor:
        raise PixelError('debug', 'Graph view belongs to a different time')
    vm = timeline.vm if timeline.cursor == len(timeline.events) else timeline.replay_vm
    frames = []
    for index, frame in enumerate(reversed(vm.call_stack)):
        graph = vm.bytecode.get('flows', {}).get(frame.function)
        if graph is None:
            continue
        info = timeline.compiled.debug['functions'][frame.function]
        frames.append(dict(frame=index, source=info['source'],
                           declaration=graph['node'], name=graph['name']))
    return dict(z=timeline.cursor, frames=frames)
