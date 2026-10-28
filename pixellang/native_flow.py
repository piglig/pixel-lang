"""State ownership and atomic commits for natively compiled flow instructions.

Expressions and nodes execute in PixelVM. This module owns instances and frozen
state; it never parses source or evaluates graph expressions.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class FlowHandle:
    identity: int
    target: str
    signature: str

    @property
    def type_name(self):
        return 'Flow<' + self.signature + '>'


@dataclass
class FlowInstance:
    handle: FlowHandle
    cells: dict = field(default_factory=dict)
    configuration: dict = field(default_factory=dict)
    steps: int = 0


@dataclass
class Transaction:
    mode: str
    instance: FlowInstance
    space_ids: set
    native_items: int
    jobs: list = field(default_factory=list)
    cursor: int = 0
    staged: dict = field(default_factory=dict)
    initial_arguments: dict = field(default_factory=dict)
    entered: dict = field(default_factory=dict)
    staged_counts: dict = field(default_factory=dict)


@dataclass(frozen=True)
class InitializationJob:
    kind: str
    path: tuple
    slot: int
    function: str
    arguments_path: tuple


@dataclass
class FlowRuntime:
    instances: dict = field(default_factory=dict)
    next_identity: int = 0
    transaction: Transaction | None = None

    def idle(self, vm, span):
        if self.transaction is not None:
            vm.fail_data('Cannot create or advance another instance inside a flow step', span,
                         code='flow.effect')

    def create(self, vm, target, span, args=()):
        self.idle(vm, span)
        parameters = vm.bytecode['flows'][target].get('initialInputs', [])
        if len(args) != len(parameters):
            vm.fail_data('Flow constructor argument count mismatch', span, code='flow.initialization')
        for value, parameter in zip(args, parameters):
            vm.typed(value, parameter['type'], span)
        signature = vm.bytecode['functions'][target]
        handle = FlowHandle(self.next_identity, target,
                            'fn(' + ','.join(signature['params']) + ')->(' + signature['result'] + ')')
        native_items = vm.spatial_items
        vm.reserve_spatial(1, span)
        self.next_identity += 1
        tx = Transaction('init', FlowInstance(handle), set(vm.space_identities), native_items)
        self.transaction = tx
        tx.initial_arguments[()] = [vm.freeze_spatial_payload(value, span) for value in args]
        pending = [(target, (), (), [])]
        expanded = 0
        while pending:
            if vm.cancelled:
                vm.fail('Execution cancelled', span)
            expanded += 1
            if expanded > vm.max_heap_objects:
                vm.fail_data('Flow instance graph exceeds the object budget', span, code='flow.limit')
            definition, path, parent_path, arguments = pending.pop()
            if len(path) >= vm.max_depth:
                vm.fail_data('Flow instance nesting exceeds call depth budget', span, code='flow.depth')
            graph = vm.bytecode['flows'][definition]
            vm.reserve_spatial(1 + len(graph.get('states', [])), span)
            if path:
                tx.initial_arguments[path] = []
                for slot, function in enumerate(arguments):
                    tx.jobs.append(InitializationJob('argument', path, slot, function, parent_path))
            for cell in graph.get('states', []):
                tx.jobs.append(InitializationJob('state', path, cell['node'], cell['initializer'], path))
            # Declaration order gives stable initialization identities independent
            # of a graph's execution schedule.
            for node in reversed(graph['children']):
                if node['function'] in vm.bytecode['flows']:
                    pending.append((node['function'], (*path, node['node']), path,
                                    node.get('initializers', [])))
        self.initialize_next(vm)

    def initialize_next(self, vm):
        tx = self.transaction
        if tx.cursor < len(tx.jobs):
            job = tx.jobs[tx.cursor]
            # Frozen constructor inputs never retain caller heap references.
            # Root each thawed value until the initializer frame owns it.
            base = len(vm.stack)
            args = []
            try:
                for frozen in tx.initial_arguments[job.arguments_path]:
                    value = vm.thaw_spatial_payload(frozen, None)
                    args.append(value)
                    vm.stack.append(value)
                vm._call(job.function, args, flow_root=tx.instance.handle.identity,
                         flow_path=job.path, flow_role='init', flow_cell=job.slot)
                vm.call_stack[-1].base = base
            finally:
                del vm.stack[base:]
        else:
            tx.instance.configuration = tx.initial_arguments
            self.instances[tx.instance.handle.identity] = tx.instance
            self.transaction = None
            vm.stack.append(tx.instance.handle)

    def advance(self, vm, handle, args, span):
        self.idle(vm, span)
        instance = self.instances.get(handle.identity)
        if instance is None or instance.handle != handle:
            vm.fail_data('Unknown flow instance', span, code='flow.instance')
        self.transaction = Transaction('step', instance, set(vm.space_identities), vm.spatial_items)
        # Instance inputs have value semantics. No caller heap object can be
        # modified by a failed invocation or retained as mutable state.
        base = len(vm.stack)
        try:
            vm.stack.extend(args)
            copied = []
            for value in args:
                frozen = vm.freeze_spatial_payload(value, span)
                copy = vm.thaw_spatial_payload(frozen, span)
                copied.append(copy)
                vm.stack.append(copy)
            # Install the frame before dropping temporary GC roots, but preserve
            # the original operand stack base.
            vm._call(handle.target, copied, flow_root=handle.identity, flow_path=(), flow_role='step')
            vm.call_stack[-1].base = base
        finally:
            del vm.stack[base:]

    def active(self, vm, frame, span):
        tx = self.transaction
        if tx is None or frame.flow_root != tx.instance.handle.identity:
            vm.fail_data('State instruction requires an active flow instance', span, code='flow.instance')
        return tx

    def configuration(self, vm, frame, index, span):
        tx = self.active(vm, frame, span)
        values = tx.instance.configuration.get(frame.flow_path, ())
        if tx.mode != 'step' or not 0 <= index < len(values):
            vm.fail_data('Unknown flow configuration input', span, code='flow.configuration')
        return vm.thaw_spatial_payload(values[index], span)

    def read(self, vm, frame, cell, span):
        tx = self.active(vm, frame, span)
        key = (frame.flow_path, cell)
        if tx.mode != 'step' or key not in tx.instance.cells:
            vm.fail_data('Unknown flow state cell', span, code='flow.state')
        return vm.thaw_spatial_payload(tx.instance.cells[key], span)

    def stage(self, vm, frame, cell, value, span):
        tx = self.active(vm, frame, span)
        key = (frame.flow_path, cell)
        if tx.mode != 'step' or key not in tx.instance.cells or key in tx.staged:
            vm.fail_data('Invalid or repeated flow state update', span, code='flow.state')
        descriptor = next(item for item in vm.bytecode['flows'][frame.function]['states']
                          if item['node'] == cell)
        vm.typed(value, descriptor['type'], span)
        tx.staged[key] = vm.freeze_spatial_payload(value, span)
        tx.staged_counts[frame.flow_path] = tx.staged_counts.get(frame.flow_path, 0) + 1

    def enter(self, vm, frame, span=None):
        """Record a stable instance activation within the current transaction."""
        tx = self.active(vm, frame, span)
        if tx.mode != 'step':
            vm.fail_data('Child activation requires a step transaction', span, code='flow.instance')
        if frame.flow_path in tx.entered:
            vm.fail_data('Flow instance already advanced in this activation', span, code='flow.step')
        tx.entered[frame.flow_path] = frame.function

    def returned(self, vm, frame, value, span):
        if frame.flow_role == 'init':
            tx = self.active(vm, frame, span)
            job = tx.jobs[tx.cursor]
            frozen = vm.freeze_spatial_payload(value, span)
            if job.kind == 'argument':
                tx.initial_arguments[job.path].append(frozen)
            else:
                tx.instance.cells[(job.path, job.slot)] = frozen
            tx.cursor += 1
            self.initialize_next(vm)
            return True
        tx = self.transaction
        if tx is None or frame.flow_root != tx.instance.handle.identity:
            return False
        if frame.function not in vm.bytecode.get('flows', {}):
            return False
        # Check each activated frame at its return. Skipped children are not
        # participants, and retain their previous cells when the root commits.
        if frame.flow_path in tx.entered and tx.entered[frame.flow_path] == frame.function:
            updates = vm.bytecode['flows'][frame.function]['updates']
            # stage() already checks cell identity and forbids duplicate keys.
            # Count by path to avoid rescanning every sibling's staged cells.
            if tx.staged_counts.get(frame.flow_path, 0) != len(updates):
                vm.fail_data('Flow step did not execute every declared state update', span, code='flow.state')
        if frame.flow_role != 'step':
            return False
        expected = {(path, update['state']) for path, function in tx.entered.items()
                    for update in vm.bytecode['flows'][function]['updates']}
        if set(tx.staged) != expected:
            vm.fail_data('Flow step has incomplete activated state updates', span, code='flow.state')
        committed = dict(tx.instance.cells)
        committed.update(tx.staged)
        tx.instance.cells = committed
        tx.instance.steps += 1
        self.transaction = None
        return False

    def abort(self, vm):
        tx = self.transaction
        if tx is not None:
            vm.space_identities = tx.space_ids
            vm.spatial_items = tx.native_items
            self.transaction = None

    def space_identity(self, vm, frame, identity, span):
        tx = self.active(vm, frame, span)
        if tx.mode != 'init':
            vm.fail_data('Space creation belongs in a flow state initializer', span, code='flow.effect')
        path = ','.join(map(str, frame.flow_path))
        return f'flow:{frame.flow_root}:{path}:{frame.flow_cell}:{identity}'
