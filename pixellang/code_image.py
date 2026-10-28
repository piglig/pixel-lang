"""Immutable, validated executable images shared by independent VM instances.

This is a host execution artifact, not a PixelLang data type. Source dictionaries
are detached before validation; mutable program state never lives in the image.
"""
import math
from .model import PixelError


def _readonly(*args, **kwargs):
    raise TypeError('Executable images are read-only')


class _Object(dict):
    __slots__ = ()
    __init__ = _readonly
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _readonly

    def __deepcopy__(self, memo):
        return self


class _Array(list):
    __slots__ = ()
    __init__ = _readonly
    __setitem__ = __delitem__ = append = extend = insert = pop = remove = clear = sort = reverse = __iadd__ = __imul__ = _readonly

    def __deepcopy__(self, memo):
        return self


def _snapshot(value, memo, active, depth=0):
    kind = type(value)
    if value is None or kind in (bool, int, str):
        return value
    if kind is float and math.isfinite(value):
        return value
    if depth > 128:
        raise PixelError('bytecode', 'Executable image nesting limit exceeded')
    if not isinstance(value, (dict, list)):
        raise PixelError('bytecode', 'Executable images require finite JSON data')
    identity = id(value)
    if identity in active:
        raise PixelError('bytecode', 'Executable images cannot contain cycles')
    if identity in memo:
        return memo[identity]
    active.add(identity)
    result = dict.__new__(_Object) if isinstance(value, dict) else list.__new__(_Array)
    memo[identity] = result
    if isinstance(value, dict):
        for key, item in value.items():
            if type(key) is not str:
                raise PixelError('bytecode', 'Executable object keys must be strings')
            dict.__setitem__(result, key, _snapshot(item, memo, active, depth + 1))
    else:
        for item in value:
            list.append(result, _snapshot(item, memo, active, depth + 1))
    active.remove(identity)
    return result


class CodeImage:
    __slots__ = ('_data', '_instructions', '_effect_functions')

    def __init__(self, bytecode):
        if hasattr(self, '_data'):
            _readonly()
        from .vm import validate_bytecode
        # Never certify caller-owned mutable dictionaries as already validated.
        data = _snapshot(bytecode, {}, set())
        validate_bytecode(data)
        object.__setattr__(self, '_data', data)
        # Call and closure checks need membership, not serialized declaration order.
        object.__setattr__(self, '_effect_functions', frozenset(data['effectFunctions']))
        # Decode immutable instruction fields once, shared by all VM instances.
        # Keep the original document for serialization, inspection and validation.
        # PUSH operands resolve to validated values, avoiding another lookup per step.
        instructions = dict.__new__(_Object)
        for name, function in data['functions'].items():
            dict.__setitem__(instructions, name, tuple(
                (ins['op'], data['constants'][ins['arg']]['value'] if ins['op'] == 'PUSH'
                 else ins.get('arg'), ins.get('span'), ins.get('node'))
                for ins in function['instructions']))
        object.__setattr__(self, '_instructions', instructions)

    __setattr__ = __delattr__ = _readonly

    @property
    def data(self):
        return self._data

    @property
    def instructions(self):
        return self._instructions

    @property
    def effect_functions(self):
        return self._effect_functions

    def __deepcopy__(self, memo):
        return self
