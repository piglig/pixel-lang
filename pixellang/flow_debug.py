"""Readonly views of expression values, persistent children and flow state."""
from . import spatial_values as spatial


def _summary(vm, value):
    if isinstance(value, spatial.FrozenValue):
        return dict(type=value.type_name, length=len(value.items), frozen=True)
    return vm.snapshot_value(value)


def inspect_flows(vm):
    result = []
    for depth, frame in enumerate(vm.call_stack):
        graph = vm.bytecode.get('flows', {}).get(frame.function)
        if graph is None: continue
        values = flow_values(vm, len(vm.call_stack) - depth - 1)

        def preview(item, value):
            return dict(node=item['node'], type=item['type'], ready=value is not None,
                        value=_summary(vm, value) if value is not None else None)

        result.append(dict(stackIndex=depth, function=frame.function, declaration=graph['node'],
            instance=values['instance'], step=values['step'], module=graph['module'], name=graph['name'],
            path=list(frame.flow_path),
            inputs={item['name']:preview(item, values['inputs'][item['name']]) for item in graph['inputs']},
            values=[dict(node=item['node'],name=item['name'],phase=values['values'][item['name']]['phase'],
                         result=preview(item,values['values'][item['name']]['result'])) for item in graph['values']],
            children=list(values['children'].values()),
            states=[dict(name=cell['name'],value=preview(cell,values['oldState'][cell['name']])) for cell in graph['states']],
            updates=[dict(node=update['node'],state=update['state'],expression=update['expression'],
                          staged=values['nextState'][cell['name']]['staged'],
                          value=_summary(vm,values['nextState'][cell['name']]['value']))
                     for update in graph['updates'] for cell in graph['states'] if cell['node']==update['state']],
            dependencies=graph['dependencies'], output=preview(graph['output'],values['output'])))
    return result


def flow_scopes(vm):
    """Attach the nearest active graph to each DAP frame, including helpers."""
    result = []
    owner = None
    count = len(vm.call_stack)
    for depth, frame in enumerate(vm.call_stack):
        graph = vm.bytecode.get('flows', {}).get(frame.function)
        if graph is not None:
            owner = (depth, graph['name'])
        if owner is not None:
            result.append(dict(frame=count - depth - 1, name=owner[1],
                               path=['flow', count - owner[0] - 1]))
    return result


def instance_values(vm, handle):
    """Committed cells retain their native frozen values; never thaw for display."""
    instance = vm.flow_runtime.instances.get(handle.identity)
    if instance is None:
        return dict(instance=handle.identity, status='unpublished')
    definitions = vm.bytecode.get('flows', {})
    cells = {}
    for (path, cell), value in instance.cells.items():
        graph = definitions[handle.target]
        names = []
        for node_id in path:
            node = next(node for node in graph['children'] if node['node'] == node_id)
            names.append(node['name'])
            graph = definitions[node['function']]
        names.append(next(item['name'] for item in graph['states'] if item['node'] == cell))
        cells['/'.join(names)] = value
    return dict(instance=handle.identity, step=instance.steps, state=cells)


def flow_values(vm, frame_index):
    """Lazy values at the selected frame; never thaw committed or staged state."""
    from .model import PixelError
    if type(frame_index) is not int or not 0 <= frame_index < len(vm.call_stack):
        raise PixelError('debug', 'Unknown flow frame')
    frame = vm.call_stack[-frame_index - 1]
    graph = vm.bytecode.get('flows', {}).get(frame.function)
    if graph is None: raise PixelError('debug', 'Frame is not a native flow')

    def read(item): return vm.binding_value(frame.memory[item['slot']])

    instructions = vm.bytecode['functions'][frame.function]['instructions']
    stores = {instruction.get('arg'):position for position,instruction in enumerate(instructions)
              if instruction['op']=='STORE'}
    start = max((stores[cell['slot']]+1 for cell in graph['states']), default=0)
    by_node = {item['node']:item for item in graph['values']}
    named = {}
    for node in graph['order']:
        item = by_node[node]
        value = read(item)
        end = stores[item['slot']]
        phase = 'complete' if value is not None else 'pending'
        if value is None and start < frame.pc <= end:
            phase = 'failed' if vm.error else 'running'
        named[item['name']] = dict(phase=phase,result=value,sourceNode=node)
        start = end + 1
    instance = vm.flow_runtime.instances.get(frame.flow_root)
    tx = vm.flow_runtime.transaction
    if tx is not None and tx.instance.handle.identity != frame.flow_root: tx = None
    staged = tx.staged if tx is not None else {}
    output = read(graph['output'])
    children = {}
    for item in graph['children']:
        path = (*frame.flow_path,item['node'])
        running = any(f.flow_root==frame.flow_root and f.flow_path==path and f.function==item['function']
                      and f.flow_role=='child' for f in vm.call_stack)
        if running: phase = 'failed' if vm.error else 'running'
        elif tx is not None and path in tx.entered: phase = 'complete'
        elif vm.error and tx is None: phase = 'unavailable'
        else: phase = 'skipped' if output is not None else 'pending'
        children[item['name']] = dict(node=item['node'],name=item['name'],function=item['function'],path=list(path),phase=phase)
    old, updates = {}, {}
    declared_updates = {item["state"] for item in graph["updates"]}
    for cell in graph['states']:
        key = (frame.flow_path,cell['node'])
        old[cell['name']] = instance.cells.get(key) if instance is not None else None
        retained = cell['node'] not in declared_updates
        updates[cell['name']] = dict(staged=key in staged, retained=retained,
                                    value=old[cell['name']] if retained else staged.get(key))
    return dict(name=graph['name'],instance=frame.flow_root,path=list(frame.flow_path),
                step=instance.steps if instance is not None else None,
                inputs={item['name']:read(item) for item in graph['inputs']},
                values=named,children=children,oldState=old,nextState=updates,output=output)
