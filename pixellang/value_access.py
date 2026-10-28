"""Scoped, non-escaping access to a caller's value binding for inout parameters."""
from .value_map import ValueMap

from dataclasses import dataclass, field
from copy import deepcopy

from .value_update import check_path, replace_path


@dataclass(frozen=True)
class Access:
    identity: int
    type_name: str


@dataclass
class _Entry:
    token: Access
    root: object
    slot: int
    path: tuple
    indices: tuple
    owner: object
    parent: int | None
    pending: bool = True


@dataclass
class ValueAccesses:
    entries: dict = field(default_factory=dict)
    next_identity: int = 0
    _roots: dict = field(default_factory=dict, init=False, repr=False, compare=False)
    _children: dict = field(default_factory=dict, init=False, repr=False, compare=False)

    def __post_init__(self):
        for entry in self.entries.values():
            self._index(entry)

    def __deepcopy__(self, memo):
        result = type(self)()
        memo[id(self)] = result
        result.entries = deepcopy(self.entries, memo)
        result.next_identity = self.next_identity
        # Checkpoints copy frames; identity indexes must refer to those copies.
        for entry in result.entries.values():
            result._index(entry)
        return result

    def _index(self, entry):
        identity = entry.token.identity
        self._roots.setdefault((id(entry.root), entry.slot), set()).add(identity)
        if entry.parent is not None:
            self._children.setdefault(entry.parent, set()).add(identity)

    def _remove(self, identity):
        entry = self.entries.pop(identity)
        key = (id(entry.root), entry.slot)
        roots = self._roots[key]
        roots.remove(identity)
        if not roots:
            del self._roots[key]
        if entry.parent is not None:
            siblings = self._children[entry.parent]
            siblings.remove(identity)
            if not siblings:
                del self._children[entry.parent]

    def _entry(self, vm, access, span=None, *, allow_children=False):
        entry = self.entries.get(access.identity)
        if entry is None or entry.token != access:
            vm.fail('Expired inout access', span)
        if not any(frame is entry.root for frame in vm.call_stack):
            vm.fail('inout root is outside its call lifetime', span)
        if not allow_children and self._children.get(access.identity):
            vm.fail('inout access is suspended by a nested borrow', span)
        return entry

    def _read(self, vm, entry, span=None):
        value = vm.binding_value(entry.root.memory[entry.slot])
        if entry.path:
            parents = check_path(vm, value, entry.path, entry.indices, span)
            _, items, key = parents[-1]
            if isinstance(items, (dict, ValueMap)) and key not in items:
                vm.fail('inout map key does not exist', span)
            value = items[key]
        return value

    def begin(self, vm, frame, slot, path=(), indices=(), span=None, *, typ=None):
        if vm.cancelled:
            vm.fail('Execution cancelled', span)
        if not any(item is frame for item in vm.call_stack):
            vm.fail('inout owner is outside its call lifetime', span)
        if type(slot) is not int or not 0 <= slot < len(frame.memory):
            vm.fail('Invalid inout root slot', span)
        if len(path) > 128:
            vm.fail('Invalid inout address path', span)
        for part in path:
            if not isinstance(part, dict) or not (
                set(part) == {'field'} and isinstance(part['field'], str) and part['field']
                or set(part) == {'index'} and part['index'] is True
            ):
                vm.fail('Invalid inout address selector', span)
        if sum('index' in part for part in path) != len(indices):
            vm.fail('Invalid inout address indices', span)
        binding = frame.memory[slot]
        if binding is None:
            vm.fail('inout root is uninitialized', span)
        parent = None
        root = frame
        if isinstance(binding, Access):
            parent_entry = self._entry(vm, binding, span)
            parent = binding.identity
            root, slot = parent_entry.root, parent_entry.slot
            path = (*parent_entry.path, *path)
            indices = (*parent_entry.indices, *indices)
        elif vm.is_cell(binding):
            vm.require_mutable(binding, span)
        if len(path) > 128:
            vm.fail('inout address path exceeds 128 selectors', span)
        ancestors = set()
        current = parent
        while current is not None:
            ancestors.add(current)
            current = self.entries[current].parent
        for identity in self._roots.get((id(root), slot), ()):
            if identity not in ancestors:
                vm.fail('Overlapping inout accesses require exclusive access', span)
        # Resolve and type-check before publishing a lock. A bad path cannot leak it.
        entry = _Entry(Access(self.next_identity, ''), root, slot,
                       tuple(dict(part) for part in path), tuple(indices), frame, parent)
        value = self._read(vm, entry, span)
        typ = typ or vm.value_type(value)
        vm.typed(value, typ, span)
        entry.token = Access(self.next_identity, typ)
        self.next_identity += 1
        self.entries[entry.token.identity] = entry
        self._index(entry)
        return entry.token

    def claim(self, vm, access, frame, span=None):
        entry = self._entry(vm, access, span)
        if not entry.pending:
            vm.fail('inout arguments require a fresh scoped borrow', span)
        if not any(item is frame for item in vm.call_stack):
            vm.fail('inout callee is outside its call lifetime', span)
        entry.owner, entry.pending = frame, False

    def read(self, vm, access, span=None):
        entry = self._entry(vm, access, span, allow_children=True)
        if any(not self.entries[identity].pending for identity in self._children.get(access.identity, ())):
            vm.fail('inout access is suspended by a nested borrow', span)
        value = self._read(vm, entry, span)
        return vm.typed(value, access.type_name, span)

    def write(self, vm, access, value, span=None):
        entry = self._entry(vm, access, span)
        vm.typed(value, access.type_name, span)
        binding = entry.root.memory[entry.slot]
        if vm.is_cell(binding):
            vm.require_mutable(binding, span)
        base = vm.binding_value(binding)
        updated = replace_path(vm, base, entry.path, entry.indices, value, span) if entry.path else value
        # Publication follows all validation and allocation, preserving the old root on failure.
        if vm.is_cell(binding):
            vm.typed(updated, vm.value_type(binding), span)
            vm.heap[binding.address]['items']['value'] = updated
        else:
            entry.root.memory[entry.slot] = updated

    def check_direct_write(self, vm, frame, slot, span=None):
        if (id(frame), slot) in self._roots:
            vm.fail('Cannot write a root during an exclusive inout access', span)

    def release_frame(self, frame, *, pending_only=False):
        if not self.entries:
            return
        removed = {identity for identity, item in self.entries.items()
                   if item.owner is frame and (not pending_only or item.pending)}
        while True:
            children = {identity for identity, item in self.entries.items() if item.parent in removed}
            if children <= removed:
                break
            removed |= children
        for identity in removed:
            self._remove(identity)

    def discard_pending(self, vm, frame, values, span=None):
        """Release abandoned argument borrows without ending the caller's accesses."""
        removed = set()
        for value in values:
            if not isinstance(value, Access):
                continue
            entry = self._entry(vm, value, span, allow_children=True)
            if entry.owner is not frame or not entry.pending:
                vm.fail('Cannot discard an active inout parameter', span)
            removed.add(value.identity)
        if any(entry.parent in removed and identity not in removed
               for identity, entry in self.entries.items()):
            vm.fail('Cannot discard an inout access with a retained child', span)
        for identity in removed:
            self._remove(identity)

    def clear(self):
        self.entries.clear()
        self._roots.clear()
        self._children.clear()

    def roots(self):
        for entry in self.entries.values():
            yield from entry.indices
