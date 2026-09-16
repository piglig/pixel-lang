# Runtime, diagnostics and trace

[中文](../../../zh-CN/reference/formats/runtime.md) · [English](runtime.md) · [Documentation](../../README.md)

`VM(bytecode,max_steps=100000,trace=False,max_depth=256)` validates and copies its executable. `step()` runs one instruction and returns state; `run()` runs until completion or a bounded failure and returns a copy of output. Calling step on a halted VM does not execute more instructions.

Source program, executable, mutable runtime state and trace are distinct objects. A trace never changes source pixels. Debug snapshots expose PC, function, operand stack, current memory, call frames, heap, output, instruction count, halt flag and error.

Error contract: `{phase,message,span}`. Span includes source, exact points, min/max. Phases: format, encoding, spatial, syntax, semantic, type, bytecode, runtime, and tooling I/O/request. Syntax uses the offending token or region; semantic/type uses the related expression/statement; runtime uses the executing instruction's provenance. File/schema failures may have no span. Dependency load errors map to the importing region. Cycles map to the import edge that closes the cycle.

CLI errors go to stderr with exit code 1; `--json` provides machine-readable diagnostics. Studio highlights only points from its current source, and names external module locations instead of highlighting unrelated coordinates. Budget errors retain partial trace/output. Each Studio debug session is independent; at most 16 are retained, oldest first eviction.


Current quotas: VM default 100,000 steps and 256 frames; 65,536 slots/function,
1,000,000 heap items and 100,000 heap objects; input/string/output limits of roughly
one million characters/units, with exact accounting in vm.py. The VM embedding API
accepts positive integer `max_heap_items` and `max_heap_objects` keyword arguments
for explicitly bounded workloads; allocation, array/map growth and split use those
limits. These settings do not change bytecode semantics or the default quotas.
They are host configuration, like step/depth limits, and are not set by programs. Timeline accepts
1..1,000,000 steps, uses a 256-transition checkpoint interval, 16 MB checkpoint and
32 MB journal budgets by default. Evicted old checkpoints may require replay from
zero. Measured random-seek latency is not a universal constant-time guarantee.

Data errors are catchable as Error; assertions, overflow, cancellation and VM
resource limits are fatal. FileAccess defaults to no read/write grants, confines
relative paths under directory capabilities and rejects symlink traversal. Effects
capture ordered read results and write outcomes within an 8 MB budget. Replay uses
captured effects and never writes files again. Cancellation is observed between
instructions and Studio service batches; it cannot interrupt a running host call.

Binary data primitives available to programs:
`utf8Encode(string) -> [int]`, `utf8Decode([int]) -> string`,
`float64Bytes(float64) -> [int]`, and `float64FromBytes([int]) -> float64`.
Bytes are integers 0..255; float representation is exactly eight big-endian IEEE
754 bytes. Signed zero is preserved; non-finite decoded values are rejected.
UTF-8 conversion is strict, including rejection of overlong/surrogate sequences.
Range/length/encoding failures are catchable (`bytes.range`, `bytes.length`,
`text.invalid_utf8`, `numeric.range`). Heap and string limits still apply. These
are general data conversion operations, not compiler or PNG-encoding shortcuts.
