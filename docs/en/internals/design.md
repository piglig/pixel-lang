# Design principles and future work

[简体中文](../../zh-CN/internals/design.md) · [English](design.md) · [Documentation](../README.md)

This page records design decisions and explicitly uncommitted directions. Language references define current behavior; verification reports establish evidence for a specific snapshot.

## Adopted principles

- **One ordinary value model.** Records, arrays, maps and enums pass by value. `let` is readonly;
  `var` permits local updates. Caller updates require explicit `inout` and call-site `&`.
  Entities, links and live flow instances have explicit identities. See [values](../reference/language/data.md)
  and [functions](../reference/language/functions.md).
- **One function model.** Ordinary computation uses `fn`; external effects require `effect`.
  Closures capture values. Caller-write permissions and external effects are checked separately;
  the absence of `effect` alone does not justify memoizing a call.
- **Explicit state advancement.** Flows separate construction from `step`, read old state,
  compute `next`, then commit atomically on success. Scheduling belongs to hosts or libraries;
  XYZ does not imply time. See [event applications](../guides/state-machines.md).
- **Space is program semantics.** Frames, positions, directions, stable entity identities and typed
  links participate in computation. Studio projects and edits one authoritative source rather than
  maintaining a second executable graph. See [native space](../guides/spatial-programming.md).
- **Explicit failure.** Option represents absence, Result represents recoverable failure, and `?`
  propagates it. Cancellation, resource limits and program faults terminate execution.
  See [errors](../reference/language/errors.md).
- **One public entry point per operation.** Basic container operations use receiver methods;
  general algorithms belong to the standard library. Collection keys follow the shared Hashable
  contract instead of an artificial string restriction. See [collections](../reference/language/collections.md).
- **Text and pixels express the same program.** PNG stores explicit semantic regions; recovery
  does not rely on hidden source. See [encoding](../reference/formats/encoding.md).

## Implementation boundaries

The resident native compiler owns binding, declaration-type, function-semantic, frame and IR query
caches. Semantic dependencies determine invalidation. Global declaration indexes, constraint validation
and specialization still contain project-wide phases; function-query reuse is not incremental execution
of every stage. See the [compiler service](../reference/compiler-service.md).

The old Python compilation and editing frontends have been removed. Python still provides execution,
debug replay, services and independent reference checks; see [Python boundaries](python.md).
Reference implementations are not compatibility fallbacks for native compilation failures.

## Future evaluation, not language commitments

1. Evaluate whether value-block control flow can follow ordinary block rules; define scope and exit
   cleanup first, without adding another block syntax.
2. Improve generic inference and diagnostics to reduce explicit type arguments, while keeping parsing
   independent of name prescanning.
3. Incrementalize global analysis and editor indexes further, measuring edit latency and invalidation scope.

## Verification and documentation responsibilities

References define rules, guides explain workflows, and this page explains decisions.
[Layered verification](../contributing/verification.md) defines checks and release requirements.
Individual test counts, timings and installation results belong in reports bound to input fingerprints,
not scattered through syntax chapters; they do not establish acceptance of later changes.
The bundled manifest identifies the compiler seed.

Organize tests by behavior and delivery boundary. Share duplicate fixtures within a boundary;
retain native/reference-VM differentials, incremental/fresh equivalence, self-host fixed points,
PNG recovery and installed Studio checks as separate responsibilities.

## Further reading

- [Documentation index](../README.md)
- [Compiler architecture](architecture.md)
