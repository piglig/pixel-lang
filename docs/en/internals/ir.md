# Intermediate representation and function reuse

[简体中文](../../zh-CN/internals/ir.md) · [English](ir.md) · [Documentation](../README.md)

IR lowers checked programs into stack operations with source provenance. It is independent of PNG layout and assembles into PixelVM bytecode.

## Programs and functions

IR stores versions, records, enums, an entry and functions. The entry takes no arguments and returns unit. Functions contain parameters, captures, local slots, result types and instructions. Instructions carry an opcode, optional operands and provenance. Assembly interns typed constants and resolves labels; constant-pool numbers are not reusable semantic identities. Generic arguments identify concrete instances, and closures lower to separate functions with typed captures.

## Lowering rules

- Evaluate arguments once, left to right; short-circuit branches preserve explicit operand state.
- Iterate the entry snapshot of a collection; `continue` targets advancement.
- Capture readonly values and release explicit `inout` access on exits and failures.
- Lower `?` through result propagation and operand cleanup, not recoverable exception handlers.
- Preserve native types, state and transaction boundaries for spatial operations and flows.

See [bytecode](../reference/formats/bytecode.md) for instruction contracts. Incomplete editor ASTs cannot produce executables.

## Query reuse

`selfhost/ir_cache.pxl` retains function queries in the native session. Keys include stable declaration/closure identities and concrete generic arguments. Reuse validates bodies, observed dependency contracts and frame layouts. Nodes, symbols, types and call targets relocate into the current project. Flow IR, initializer functions and graph metadata reuse together.

Caches retain structural values without disk JSON serialization. Missing queries, mismatched identities or capacity pressure cause fresh lowering; only successful snapshots publish. Fresh compilation provides the correctness comparison. See the [compiler service](../reference/compiler-service.md) for statistics and boundaries.
