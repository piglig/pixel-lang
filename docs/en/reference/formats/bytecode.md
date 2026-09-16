# PixelVM 0.8

[中文](../../../zh-CN/reference/formats/bytecode.md) · [English](bytecode.md) · [Documentation](../../README.md)

The VM validates and copies a PIXELVM executable with matching 0.8 language and VM
axes. Compiler version is informational. Sections are constants, nominal record
schemas (including validated built-in Error), concrete enum schemas, entry and
function bodies with explicit parameter and captured-cell signatures.

Machine state includes call frames with PC/memory/operand base/handlers, shared
operand stack, typed heap references, constants, output, instruction count and
halt/error status. Entry is one parameterless unit function. Return checks frame
balance; a unit return uses an internal sentinel. Nested calls cannot pop caller
operands. Heap values include arrays/maps/records, enum payloads, function environments and
shared binding cells; json is immutable and tagged.

Instructions: PUSH, LOAD, STORE, BIND, CAPTURE; checked arithmetic/comparison/logical operations;
DUP/DUP2/POP/PRINT; CALL/FUNCTION/CLOSURE/INVOKE/RETURN; JUMP/JUMP_IF_FALSE/JUMP_IF_TRUE/TRAP;
ARRAY/MAP/RECORD, ENUM/ENUM_IS/ENUM_GET, INDEX/INDEX_SET, FIELD/FIELD_SET; BUILTIN; TRY/END_TRY;
ITER_SNAPSHOT with explicit single/pair mode; JSON_DECODE with a target type.
The exact validated operands and dynamic behavior live in vm.py and backend.py.

Validation rejects unknown versions/opcodes, malformed schemas/constants, invalid
slot/constant/jump targets, unknown calls and invalid entry signatures. Dynamic
checks enforce operand types, uninitialized memory, frame bounds, integer overflow
and resource budgets. Arbitrary external bytecode is not statically proven safe;
invalid execution raises a structured diagnostic rather than host code execution.

GC marks from frames, operand stack and transient allocation roots, then sweeps.
Deterministic identities and complete checkpoints support replay across GC. See
[RUNTIME_SPEC.md](runtime.md) for quotas and error/replay semantics.
