# Dataflow and transactions

[简体中文](../../zh-CN/internals/dataflow.md) · [English](dataflow.md) · [Documentation](../README.md)

Flows combine declaration identity, dependency ordering and state commits. This page explains implementation constraints; the state-machine guide covers application development.


**On this page**

- [Computation and dependencies](#computation-and-dependencies)
- [Persistent child instances](#persistent-child-instances)
- [State, events and commits](#state-events-and-commits)
- [Effects and debugging](#effects-and-debugging)
- [PNG delivery](#png-delivery)
- [Further reading](#further-reading)

## Computation and dependencies

Ordinary functions prohibit implicit external effects. Local `var` bindings modify
their own values; parameter writeback requires `inout`.

```pixellang
fn Double(values: [int]) -> [int] {
    var result: [int] = []
    for value in values { result.append(value * 2) }
    return result
}
```

Flows use expressions whose dependencies form a native graph. Creation parameters and step inputs are separate:
`Counter(0)` creates an instance, and `counter.step(2)` advances it. Even stateless
flow calls construct instances; use `fn` for ordinary direct computation.

```pixellang
flow Counter(initial: int) step(delta: int) -> int {
    state value = initial
    let updated = value + delta
    next value = updated
    output updated
}
```

Pure calls do not create persistent instances. Stateful children require explicit,
stable identities that cannot depend on expression order, common-subexpression
elimination or execution count. AST and IR retain dependencies, state, instances
and commit boundaries. Generated temporary source or reparsed JSON graphs must
not substitute for these native structures.

### Expression dependency analysis

Per-step flow names are predeclared, allowing forward references between named values.
The compiler orders calculations by dependency, choosing ready calculations in source order.
Edges retain declaration, reference and consuming AST identities. Lambda parameters and pattern bindings
that shadow a flow name do not introduce false edges; closure captures and record shorthand do contribute dependencies.
Constructor parameters are read-only configuration available during initialization and per-step computation. State and instance initializers cannot observe step inputs,
other state cells or named step calculations. Reading old state does not create a combinational feedback cycle.
Native parsing, binding, checking and execution enforce these rules together.

## Persistent child instances

`instance counter = Counter(initial)` creates a child when its parent is created; the target must be a visible flow declaration.
Its identity belongs to the declaration, independently of expression scheduling. It cannot escape into aliases, ordinary arguments or callbacks.
`counter.step(delta)` may execute at most once on an activation path. Bind its result to reuse it; mutually exclusive match arms may each step the child.
A skipped child retains its state. All child and parent next values share one commit boundary, and any execution fault must leave every state uncommitted.
Creation, per-step computation and commit remain distinct in native IR; ordinary calls with hidden global handles are not an implementation substitute.
Native checks and execution enforce types, per-path step counts and rollback.

## State, events and commits

State belongs to an explicitly created runtime instance. Copying a handle refers
to that instance; creating a new instance allocates independent state. Each step
reads the old snapshot, computes candidate `next` values and `output`, then commits
all changes together. Nested children commit atomically with their parent.
Execution faults must not publish partial state or history.

A business `Err` is an ordinary result, not an implicit rollback of deliberately
selected next state. Transitions must handle rejected events explicitly. Ordinary
data has value semantics: updates cannot change inputs, earlier outputs or history.

The native contract permits at most one `next` per state cell. An omitted update
retains the old value without generating instructions or graph edges.
Initializers may use constructor inputs or permitted constant
expressions, but cannot read step inputs or perform arbitrary external I/O.
Feedback crosses a logical step through a state cell; stateless cycles are invalid.

Events and logical clocks are explicit inputs; clocks are optional. Fixed-step scheduling
is a driver policy, not a flow type requirement. Hosts select a root instance and schedule events through application configuration; programs advance explicitly with `instance.step(...)`. See [state machines](../guides/state-machines.md). XYZ retains spatial
meaning; placing history along a display axis does not change program coordinates.

## Effects and debugging

I/O, implicit time and implicit randomness cannot hide inside pure computation;
external operations use `effect fn`. Callbacks and cross-module calls preserve
effect permissions. Spatial state construction grants only restricted instance
construction permission, not file access or other external capabilities.

Independent computations, error selection and traces must be deterministic.
Future parallel execution must preserve this contract. Debugging exposes input
provenance, step outputs and before/after state; replay does not repeat external writes.

Studio edits the same program semantics through source and graph views, without a
second authoritative connection JSON. View layout, camera and zoom do not change
results. Native graph edits can change outputs, states and child creation arguments. Real pointer and keyboard undo acceptance requires the separate desktop interaction gate.

## PNG delivery

Current native flows encode as semantic pixels, recover to source and recompile.
Named calculations, state and inputs have explicit identities; cross-module targets and
record projections do not depend on optional name metadata. Invalid regions and
nonexistent references must be rejected before execution. New syntax must meet the same
roundtrip and diagnostic requirements.

Verify imported flows, nested instances, spatial initializers, debug maps and restored history against the current source and artifact identities.

## Further reading

- [Documentation index](../README.md)
- [Compiler architecture](architecture.md)
