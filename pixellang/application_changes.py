"""Bounded presentation of spatial state changes at an application boundary."""
import json
from .spatial_values import SpaceValue, FrozenValue
from .spatial_diff import compare_spaces
from .typeexpr import is_typed_map
from .typesys import is_record
from .model import PixelError

MAX_ROWS = 100
MAX_SPACES = 64
MAX_SCAN = 10000
MAX_TEXT = 8000


def spaces_in_state(vm):
    result, remaining = {}, MAX_SCAN
    roots = ((f'Instance {instance_id} / {list(path)} / state {node}', value)
             for instance_id, instance in sorted(vm.flow_runtime.instances.items())
             for (path, node), value in sorted(instance.cells.items()))
    pending = [iter(roots)]
    while pending:
        try:
            path, value = next(pending[-1])
        except StopIteration:
            pending.pop()
            continue
        remaining -= 1
        if remaining < 0:
            return result, True
        if isinstance(value, SpaceValue):
            if len(result) == MAX_SPACES:
                return result, True
            result[path] = value
        elif isinstance(value, FrozenValue):
            pairs = value.items if is_record(value.type_name) or is_typed_map(value.type_name) else enumerate(value.items)
            # Bind the parent path now; suspended generators must not capture the loop variable.
            def children(parent, items):
                return ((f'{parent}[{key!r}]', child) for key, child in items)
            pending.append(children(path, pairs))
    return result, False


def compact_spaces(value):
    if isinstance(value, dict):
        if isinstance(value.get('identity'), str) and isinstance(value.get('version'), int) and isinstance(value.get('entities'), list) and isinstance(value.get('links'), list):
            return dict(space=value['identity'], version=value['version'],
                        entities=len(value['entities']), links=len(value['links']))
        return {key: compact_spaces(child) for key, child in value.items()}
    if isinstance(value, list):
        return [compact_spaces(child) for child in value]
    return value


def text(value):
    encoded = json.dumps(value, ensure_ascii=False, indent=2)
    return encoded if len(encoded) <= MAX_TEXT else encoded[:MAX_TEXT] + '\n… (value truncated)'


def value_preview(value, runtime):
    if value is None:
        return 'Absent'
    vm = runtime.vm
    budget = vm.output_budget
    try:
        vm.output_budget = MAX_TEXT
        return text(vm.materialize(value))
    except PixelError:
        return 'Value exceeds preview budget. Use Debug event to inspect it.'
    finally:
        vm.output_budget = budget


def describe_changes(before, after, runtime):
    previous, old_truncated = before
    current, new_truncated = after
    groups, remaining = [], MAX_ROWS
    for path in sorted(previous.keys() | current.keys()):
        old, new = previous.get(path), current.get(path)
        # Different space identities represent replacement, never a cross-space diff.
        pairs = [(old, new)] if old is None or new is None or (old.identity, old.frame, old.payload_type, old.link_type) == (new.identity, new.frame, new.payload_type, new.link_type) else [(old, None), (None, new)]
        for left, right in pairs:
            owner = right or left
            empty = SpaceValue(owner.identity, owner.frame, owner.payload_type, owner.link_type)
            difference = compare_spaces(left or empty, right or empty)
            group = dict(path=path, identity=owner.identity, beforeVersion=left.version if left else None,
                         afterVersion=right.version if right else None, rows=[], counts={})
            for category, changes in (('entity', difference.entities), ('link', difference.links)):
                for kind in ('added', 'removed', 'updated'):
                    group['counts'][category + '.' + kind] = sum(change.kind == kind for change in changes)
                for change in changes:
                    if not remaining:
                        break
                    remaining -= 1
                    group['rows'].append(dict(category=category, kind=change.kind, key=change.key,
                        fields=list(change.fields),
                        beforeText=value_preview(change.before, runtime),
                        afterText=value_preview(change.after, runtime)))
            group['total'] = len(difference.entities) + len(difference.links)
            group['truncated'] = group['total'] > len(group['rows'])
            groups.append(group)
    return dict(spaces=groups, truncated=old_truncated or new_truncated,
                total=sum(group['total'] for group in groups))
