"""Flow boundary protection and atomic, bounded value-graph copies."""
from .value_map import ValueMap



class _Work:
    def __init__(self, vm, span=None, maximum=None, cancelled=lambda: False):
        if maximum is not None and (type(maximum) is not int or maximum <= 0):
            raise ValueError('Value graph work budget must be a positive integer')
        self.vm, self.span, self.cancelled = vm, span, cancelled
        self.remaining = maximum if maximum is not None else 6 * (vm.max_heap_items + vm.max_heap_objects + 1)

    def check(self, amount=1):
        self.remaining -= amount
        if self.remaining < 0:
            self.vm.fail('Value graph work budget exceeded', self.span)
        if self.vm.cancelled or self.cancelled():
            self.vm.fail('Execution cancelled', self.span)


def reachable(vm, values, *, work=None):
    from .vm import LEAF_CONTAINERS, Ref
    from .map_keys import Key

    work = work or _Work(vm)
    # Walk edges lazily: a wide container must not allocate a second list of
    # all its children before cancellation or the work budget can be checked.
    pending = [iter(values)]
    addresses = set()
    while pending:
        try:
            value = next(pending[-1])
        except StopIteration:
            pending.pop()
            continue
        work.check()
        if not isinstance(value, Ref) or value.address in addresses:
            continue
        addresses.add(value.address)
        obj = vm.heap[value.address]
        if not obj.get('cell') and obj['type'] in LEAF_CONTAINERS:
            continue
        items = obj['items']
        if isinstance(items, (dict, ValueMap)):
            pending.append((key.value for key in items if isinstance(key, Key)))
            pending.append(iter(items.values()))
        else:
            pending.append(iter(items))
    return addresses


def protect(vm, values):
    addresses = tuple(sorted(reachable(vm, values)))
    for address in addresses:
        vm.readonly_counts[address] = vm.readonly_counts.get(address, 0) + 1
    return addresses


def release(vm, frame):
    for address in frame.protected:
        count = vm.readonly_counts.get(address, 0)
        if count > 1:
            vm.readonly_counts[address] = count - 1
        else:
            vm.readonly_counts.pop(address, None)


def publish(vm, frame, value, span):
    """Detach borrowed output graphs, retaining cycles and shared children."""
    from .vm import Ref

    if not frame.protected or not isinstance(value, Ref):
        return value
    addresses = reachable(vm, (value,))
    if addresses.isdisjoint(frame.protected):
        return value
    return copy_graph(vm, (value,), span, addresses=addresses)[0]


def copy_graph(vm, values, span, *, addresses=None,
               max_work=None, cancelled=lambda: False, _work=None):
    """Copy one shared graph for a value boundary, including binding cells.

    Captures must be copied together: separate copies would lose aliases between
    bindings. Native immutable values can be shared; every mutable VM reference
    is remapped, including cycles and closure environments.
    """
    from .vm import LEAF_CONTAINERS, Ref
    from .map_keys import Key

    values = tuple(values)
    work = _work or _Work(vm, span, max_work, cancelled)
    work.check()
    if addresses is None:
        addresses = reachable(vm, values, work=work)
    size = sum(len(vm.heap[address]['items']) for address in addresses)
    if (vm.heap_items + size > vm.max_heap_items or
            len(vm.heap) + len(addresses) > vm.max_heap_objects):
        vm.collect(values)
    if (vm.heap_items + size > vm.max_heap_items or
            len(vm.heap) + len(addresses) > vm.max_heap_objects):
        vm.fail('Heap budget exceeded while copying value graph', span)
    # Reserve the entire graph before installing edges, including cyclic edges.
    copies = {address: Ref(vm.next_address + index)
              for index, address in enumerate(sorted(addresses))}

    def remap(item):
        # References outside the selected graph retain their identity.
        return copies.get(item.address, item) if isinstance(item, Ref) else item

    staged = {}
    for address, target in copies.items():
        work.check()
        obj = vm.heap[address]
        items = obj['items']
        if not obj.get('cell') and obj['type'] in LEAF_CONTAINERS:
            # Validated scalar storage has no references to remap. Keep bulk
            # copying fast, checking cancellation before and after publication.
            work.check(len(items))
            storage = items.copy()
        elif isinstance(items, (dict, ValueMap)):
            storage = {}
            for key, item in items.items():
                work.check()
                storage[Key(key.identity, remap(key.value)) if isinstance(key, Key) else key] = remap(item)
        elif isinstance(items, bytearray):
            work.check(len(items))
            storage = bytearray(items)
        else:
            storage = []
            for item in items:
                work.check()
                storage.append(remap(item))
        staged[target.address] = {**obj, 'items': storage}
    _publish_storage(vm, staged, size, work)
    return [remap(value) for value in values]


def _publish_storage(vm, staged, size, work):
    """Commit reserved new addresses, rolling back even a partial heap update."""
    work.check()
    try:
        vm.heap.update(staged)
        work.check()
    except BaseException:
        for address in staged:
            vm.heap.pop(address, None)
        raise
    vm.next_address += len(staged)
    vm.allocations_since_gc += len(staged)
    vm.heap_items += size
