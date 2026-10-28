# Value storage and access

[简体中文](../../zh-CN/internals/value-storage.md) · [English](value-storage.md) · [Documentation](../README.md)

Value isolation is a language guarantee; structural sharing is an implementation strategy. This page connects storage, update paths, access permissions and snapshots.

## One access model

Ordinary records, arrays, maps and enums have value semantics. `let` is readonly;
`var` may update its own fields and elements. Assignment, arguments, returns and
value captures cannot introduce observable shared mutation. Reading a `Box[T]`
field or a `[T]` element still produces `T`, without a `Frozen[T]` permission projection.

Ordinary parameters are readonly. Caller writeback requires `inout`, with an explicit
`&` at the call. Writable access cannot escape, enter a container or be captured.
Access remains exclusive from argument evaluation until the call returns. The current
implementation rejects overlapping writes to the same root binding. A nested call in
an earlier argument may finish before a later argument borrows that root.

Ordinary values do not support arbitrary mutable reference cycles. Entities and links
use explicit identities for graph cycles. A live flow handle refers to an instance;
copying the handle does not create an instance. Spatial snapshots and history retain
value isolation.

## Structural sharing and updates

Isolation does not require a deep copy on every assignment. The runtime may share
immutable storage and copy only affected paths during updates. Arrays currently use
a persistent vector; maps use a hash trie with stable iteration order. Old values
remain available to snapshots and replay. Record fields and nested indexes use the
same value-update mechanism to write back the root binding.

The new semantics do not require explicit freezing or thawing. The checker, bytecode
validator and runtime jointly enforce that:

- Local updates cannot bypass readonly bindings or ordinary parameter permissions.
- Explicit parameter writeback cannot obtain access to unrelated root bindings.
- Closures retain creation-time values despite later outer-variable updates.
- Later state updates cannot alter previous outputs, spatial snapshots or checkpoints.

Sharing does not mean zero allocation. Complexity, memory accounting and cancellation
still apply. Validation may reuse verified shared branches, but changed entries and
paths remain checked; performance cannot justify bypassing type or resource boundaries.

Persistent arrays charge live shared tree slots, including internal pointers, once per storage node. Collection releases the charge after its last storage owner disappears; checkpoint restoration rebuilds the identity table. Plain containers and maps still use conservative per-container item accounting. Object and heap limits remain enforced, and failed allocation must not publish partial updates.

## Use inside the compiler

The compiler follows the same rules. Local builders use ordinary collections and
explicit `inout`, then publish values directly. Separating formerly aliased contexts
requires reviewing their ownership assumptions:

- Return the updated plan owned by a context, not an earlier copy used to construct it.
- Update a record in an array through an explicit element write; changing a loop value
  does not update the source array.
- Finish initializers and type metadata before publishing a result; later shared writes
  must not be needed to complete it.
- Recursive checks, higher-order callbacks and failure diagnostics must preserve explicit
  writeback and the correct source location.

The central type arena and its callers now use values. Type analysis, checking and
instance planning return their completed results; backend passes explicitly write back
when extending the type arena. Readonly queries do not take write access. Queries that
need newly interned types run interning before reading the updated arena, avoiding a
stale snapshot captured by earlier argument evaluation.

Compiler and runtime retain no legacy permission types or freeze/thaw conversions. Same-name user declarations follow ordinary name resolution.

## Verification boundaries

Verification covers assignment, nested updates, function boundaries, callbacks,
generics, failure paths, spatial state and historical snapshots. Source and recovered
PNG programs must enforce the same contract. A self-compilation fixed point requires
an identical, complete source snapshot. Internal type checks and snapshot tests do
not substitute for generated-compiler, full PNG or Studio acceptance.

Measure compilation, value updates, graph state and PNG export/recovery separately.
Concurrent development timings only help diagnosis. Final comparisons must follow
the power, cache and load conditions in [performance verification](../contributing/performance.md).

## Further reading

- [Documentation index](../README.md)
- [Compiler architecture](architecture.md)
