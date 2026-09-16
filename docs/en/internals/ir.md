# IR 0.8

[中文](../../zh-CN/internals/ir.md) · [English](ir.md) · [Documentation](../README.md)

Representation-independent stack IR contains version, records, enums, entry and functions.
Entry identifies exactly one parameterless unit root function; there are no module
initializers. Each function has params, captured-cell types, locals, result and instructions.

Instructions carry op, optional arg and provenance. CONST and LABEL are symbolic;
assembly interns typed constants, resolves labels and emits bytecode. Numeric IDs
identify functions/modules and fixed slots identify locals. No RGBA operation is
needed by lowering or execution. Optimization removes jumps to the next label.

Operands/arguments evaluate left to right. Short-circuit logic uses DUP, conditional
jump and POP. Collection iteration snapshots before its loop and uses internal
slots for index/keys/values. Continue targets the advancement block. Break/continue
emit END_TRY only for handlers exited by the transfer. JSON_DECODE carries the
resolved target schema type. Incomplete editor ASTs cannot enter executable builds.

Concrete generic instances share code for repeated argument tuples. Anonymous
functions lower to separate checked bodies. CAPTURE/CLOSURE preserve shared binding
identity, BIND establishes fresh declarations, and INVOKE carries the complete
callable signature. Enum constructors and matches use typed ENUM operations.

## Reusing lowered functions

`ir_cache.pxl` matches canonical module paths, declaration identities, concrete
generic arguments and closure locations. Reuse also requires matching source and
transitive dependency-interface stamps supplied by the service. All semantic
checks and frame planning still run on the current program.

The current plans determine executable IDs. Cached function references, nominal
types and instruction module IDs are relocated; source positions are retained
only for unchanged module text. Constants remain symbolic until fresh assembly,
and string payloads are never treated as identifiers. The program's schemas and
entry are rebuilt from the current plans, not copied from the cached program.

Function payloads are loaded lazily and remain independently subject to JSON
limits. Unchanged payloads can be retained under separate prior-file names; changed
payloads are serialized anew. Cache absence, noncanonical paths or an oversized
function leave ordinary lowering available. The service publishes only complete
successful snapshots. See [the service contract](../reference/compiler-service.md).
