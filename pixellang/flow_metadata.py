"""Validate expression-flow identities and provenance against native instructions.

Metadata describes declarations and values; it is not a second executable graph.
Expressions, branches and calls execute only as PixelVM instructions.
"""


def validate_flows(bytecode, fail, executable_type):
    flows = bytecode.get('flows', {})
    functions = bytecode['functions']
    effects = set(bytecode['effectFunctions'])

    def require(condition, message):
        if not condition: fail('Invalid flow metadata: ' + message)

    def node_id(value): return type(value) is int and value >= 0
    def identifier(value): return isinstance(value, str) and value.isidentifier()
    def sequence(value, label, limit=65536):
        require(isinstance(value, list) and len(value) <= limit, label)
        return value

    require(isinstance(flows, dict) and len(flows) <= 65536, 'flow section')
    require(all(isinstance(name, str) and name in functions and name not in effects
                and isinstance(graph, dict) for name, graph in flows.items()), 'flow declaration')
    initial_signatures = {}
    for name, graph in flows.items():
        initial = sequence(graph.get('initialInputs'), 'constructor inputs', 1024)
        require(all(isinstance(item, dict) and executable_type(item.get('type'))
                    and item['type'] != 'unit' for item in initial), 'constructor input type')
        initial_signatures[name] = [item['type'] for item in initial]
    initializers, argument_initializers = set(), set()
    dependencies = {name: [] for name in flows}
    stateful = {}

    def initializer(name, params, result):
        require(isinstance(name, str) and name in functions and name not in flows
                and name not in effects and name not in initializers, 'state initializer')
        fn = functions[name]
        require(fn['params'] == params and not fn.get('captures') and result == fn['result']
                and result != 'unit', 'initializer signature')
        initializers.add(name)

    def inputs(items, signature):
        sequence(items, 'inputs', 1024)
        require(len(items) == len(signature), 'input count')
        names, nodes = set(), set()
        for slot, (item, typ) in enumerate(zip(items, signature)):
            require(isinstance(item, dict) and identifier(item.get('name')) and item['name'] not in names,
                    'input name')
            require(node_id(item.get('node')) and item['node'] not in nodes, 'input node')
            require(type(item.get('slot')) is int and item['slot'] == slot and item.get('type') == typ,
                    'input signature')
            names.add(item['name']); nodes.add(item['node'])
        return names, nodes

    for name, graph in flows.items():
        fn = functions[name]
        require(node_id(graph.get('module')) and node_id(graph.get('node'))
                and node_id(graph.get('returnNode')) and identifier(graph.get('name')), 'flow identity')
        require(not fn.get('captures') and type(graph.get('stateful')) is bool, 'flow contract')
        require('nodes' not in graph and 'connections' not in graph, 'retired port wiring')
        names, roots = inputs(graph.get('inputs'), fn['params'])
        _, initial_nodes = inputs(graph['initialInputs'], initial_signatures[name])
        require(not roots & initial_nodes, 'constructor input identity')
        roots |= initial_nodes
        slots = {item['node']: item['slot'] for item in graph['inputs']}
        used_slots = set(slots.values())
        declarations = set(roots)
        instructions = fn['instructions']
        require(all(item.get('module') == graph['module'] and node_id(item.get('node'))
                    for item in instructions), 'instruction source identity')

        def declaration(item):
            require(isinstance(item, dict) and node_id(item.get('node')) and item['node'] not in declarations,
                    'declaration identity')
            require(identifier(item.get('name')) and item['name'] not in names, 'declaration name')
            declarations.add(item['node']); names.add(item['name'])

        def slot(item, allow_unit=False):
            require(type(item.get('slot')) is int and 0 <= item['slot'] < fn['locals']
                    and item['slot'] not in used_slots, 'value slot')
            require(executable_type(item.get('type')) and (allow_unit or item['type'] != 'unit'), 'value type')
            used_slots.add(item['slot'])
            slots[item['node']] = item['slot']

        cells = {}
        for cell in sequence(graph.get('states'), 'state cells'):
            declaration(cell); slot(cell)
            initializer(cell.get('initializer'), initial_signatures[name], cell['type'])
            cells[cell['node']] = cell
        stateful[name] = bool(cells or initial_signatures[name])
        children = {}
        for child in sequence(graph.get('children'), 'child declarations'):
            declaration(child)
            target = child.get('function')
            require(isinstance(target, str) and target in flows, 'child flow target')
            arguments = sequence(child.get('initializers'), 'child constructor arguments', 1024)
            require(len(arguments) == len(initial_signatures[target]), 'child constructor arity')
            for argument, typ in zip(arguments, initial_signatures[target]):
                initializer(argument, initial_signatures[name], typ)
                argument_initializers.add(argument)
            children[child['node']] = child
            dependencies[name].append(target)
        stateful[name] = stateful[name] or bool(children)
        values = {}
        for value in sequence(graph.get('values'), 'named values'):
            declaration(value); slot(value)
            require(node_id(value.get('expression')), 'value expression')
            values[value['node']] = value
        order = sequence(graph.get('order'), 'value order')
        require(all(node_id(value) for value in order) and len(order) == len(values)
                and set(order) == set(values), 'value order')
        updates, updated = {}, set()
        for update in sequence(graph.get('updates'), 'state updates'):
            require(isinstance(update, dict) and node_id(update.get('node'))
                    and update['node'] not in declarations and update['node'] not in updates
                    and node_id(update.get('expression')), 'update identity')
            require(node_id(update.get('state')) and update['state'] in cells
                    and update['state'] not in updated, 'state update')
            updated.add(update['state']); updates[update['node']] = update
        output = graph.get('output')
        require(isinstance(output, dict) and node_id(output.get('node')), 'output expression')
        slot(output, allow_unit=True)
        require(output['type'] == fn['result'], 'output type')
        require(graph['returnNode'] not in declarations and graph['returnNode'] not in updates, 'output identity')

        # Validate provenance without prescribing the expression instruction
        # sequence: ordinary branches, calls and temporaries remain legal.
        stores, reads, stages, steps, loads = {}, {}, {}, {}, set()
        for index, instruction in enumerate(instructions):
            op, arg, node = instruction['op'], instruction.get('arg'), instruction.get('node')
            if op == 'STORE': stores.setdefault(arg, []).append((node, index))
            if op == 'LOAD': loads.add((arg, node))
            if op == 'FLOW_CONFIG':
                require(type(arg) is int and 0 <= arg < len(graph['initialInputs'])
                        and node == graph['initialInputs'][arg]['node'], 'configuration input identity')
            if op == 'FLOW_READ':
                require(arg in cells and node == arg and arg not in reads, 'state read identity')
                reads[arg] = index
            if op == 'FLOW_STAGE':
                require(node in updates and updates[node]['state'] == arg and arg not in stages, 'state stage identity')
                stages[arg] = index
            if op == 'FLOW_CHILD_STEP':
                require(arg['node'] in children, 'unknown child step')
                steps[(arg['node'], arg['reference'])] = index
            if op in ('FLOW_NEW', 'FLOW_STEP'):
                require(False, 'external instance operation inside flow')
        require(set(reads) == set(cells) and set(stages) == updated, 'state instructions')
        for cell in cells.values():
            require(stores.get(cell['slot']) == [(cell['node'], reads[cell['node']] + 1)], 'state read slot')
        positions = []
        for node in order:
            value = values[node]
            entries = stores.get(value['slot'], [])
            require(len(entries) == 1 and entries[0][0] == node, 'named value store')
            positions.append(entries[0][1])
        require(positions == sorted(positions), 'value instruction order')
        require(stores.get(output['slot']) == [(output['node'], len(instructions) - 3)], 'output store')
        require(len(instructions) >= 3 and instructions[-2]['op'] == 'LOAD'
                and instructions[-2].get('arg') == output['slot'] and instructions[-2].get('node') == output['node']
                and instructions[-1]['op'] == 'RETURN' and instructions[-1].get('node') == graph['returnNode'], 'output return')
        order_position = {node: i for i, node in enumerate(order)}
        targets = set(values) | set(updates) | {graph['returnNode']}
        for edge in sequence(graph.get('dependencies'), 'dependencies', 1_000_000):
            require(isinstance(edge, dict) and node_id(edge.get('source')) and edge['source'] in declarations
                    and node_id(edge.get('target')) and edge['target'] in targets
                    and node_id(edge.get('reference')), 'dependency identity')
            source, target, reference = edge['source'], edge['target'], edge['reference']
            if source in values and target in values:
                require(order_position[source] < order_position[target], 'cyclic value dependency')
            # Captures may read bindings at a lambda-construction instruction,
            # rather than at the nested body reference's original AST position.
            if source in children:
                require((source, reference) in steps, 'child dependency instruction')

    parents = {name: [] for name in flows}
    pending = {name: len(children) for name, children in dependencies.items()}
    for name, children in dependencies.items():
        for child in children: parents[child].append(name)
    ready = [name for name, count in pending.items() if not count]
    for child in ready:
        for parent in parents[child]:
            stateful[parent] = stateful[parent] or stateful[child]
            pending[parent] -= 1
            if not pending[parent]: ready.append(parent)
    require(len(ready) == len(flows), 'recursive child instances')
    for name, graph in flows.items(): require(graph['stateful'] == stateful[name], 'stateful dependency flag')
    for name, fn in functions.items():
        for instruction in fn['instructions']:
            op, arg = instruction['op'], instruction.get('arg')
            if name in initializers:
                require(op not in {'CALL','INVOKE','FUNCTION','CLOSURE','FLOW_NEW','FLOW_STEP',
                                   'FLOW_CHILD_STEP','FLOW_CONFIG','FLOW_READ','FLOW_STAGE','PRINT','BUILTIN'}, 'initializer effect')
            if name in argument_initializers: require(op != 'SPACE_NEW', 'nested constructor identity effect')
            if op in ('FLOW_CONFIG','FLOW_READ','FLOW_STAGE','FLOW_CHILD_STEP'): require(name in flows, 'flow operation outside a flow')
            if op in ('CALL','FUNCTION','CLOSURE'):
                require(arg not in initializers, 'initializer called outside instance creation')
                require(arg not in flows or not stateful[arg], 'stateful flow used as an ordinary function')
