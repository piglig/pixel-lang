"""Compact instruction journal: shared provenance, incremental execution records."""

import struct
import sys
from collections.abc import Sequence
from copy import deepcopy

from .model import PixelError


def footprint(value, seen=None):
    seen = set() if seen is None else seen
    identity = id(value)
    if identity in seen:
        return 0
    seen.add(identity)
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(footprint(k, seen) + footprint(v, seen) for k, v in value.items())
    elif isinstance(value, (list, tuple, set)):
        size += sum(footprint(v, seen) for v in value)
    elif hasattr(value, "__dict__"):
        size += footprint(vars(value), seen)
    return size


class InstructionJournal(Sequence):
    def __init__(self, bytecode, budget=32_000_000):
        self.bytecode = bytecode
        self.records = bytearray()
        self.functions = tuple(bytecode["functions"])
        self.function_ids = {name: i for i, name in enumerate(self.functions)}
        self.errors = {}
        self.budget = budget
        self.bytes = footprint(
            (self.records, self.functions, self.function_ids, self.errors)
        )

    def __len__(self):
        return len(self.records) // 12

    def __eq__(self, other):
        return list(self) == list(other)

    @staticmethod
    def make_record(function, pc, steps, error=None):
        compact_error = (
            {
                "phase": error["phase"],
                "message": error["message"],
                "has_span": error.get("span") is not None,
            }
            if error
            else None
        )
        return (function, pc, steps, compact_error)

    def record_cost(self, error=None):
        # 12 payload bytes plus conservative capacity/index overhead. Sparse errors
        # include dictionary growth and key storage; provenance stays shared.
        return 32 + (footprint(error) + 256 if error else 0)

    def append(self, function, pc, steps, error=None):
        compact = self.make_record(function, pc, steps, error)[3]
        size = self.record_cost(compact)
        if self.bytes + size > self.budget:
            raise PixelError("temporal", "Recording budget exceeded")
        packed = struct.pack("<III", self.function_ids[function], pc, steps)
        index = len(self)
        self.records.extend(packed)
        if compact:
            self.errors[index] = compact
        self.bytes += size

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        function_id, pc, steps = struct.unpack_from("<III", self.records, index * 12)
        function, error = self.functions[function_id], self.errors.get(index)
        instruction = self.bytecode["functions"][function]["instructions"][pc]
        span = instruction.get("span") or {}
        result = {
            "z": index + 1,
            "function": function,
            "pc": pc,
            "steps": steps,
            "op": instruction["op"],
            "span": deepcopy(span),
            "points": [[*point[:2], index + 1] for point in span.get("points", [])],
        }
        if error:
            result["error"] = {
                "phase": error["phase"],
                "message": error["message"],
                "span": deepcopy(span) if error["has_span"] else None,
            }
        return result
