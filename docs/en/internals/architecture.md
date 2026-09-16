# Compiler architecture

[中文](../../zh-CN/internals/architecture.md) · [English](architecture.md) · [Documentation](../README.md)

Status: current architecture. Language behavior is defined by the
[language reference](../reference/language/overview.md); this page describes its implementation.

## Shared compiler path

CLI and VS Code Studio call `CompilerClient` → supervised `CompilerWorker` →
`CompilerService`. The service executes a pinned, hash-checked compiler bytecode
artifact using the Python PixelVM. It does not bootstrap a compiler per request.

The compiler in `selfhost/` parses text, builds the module graph, binds symbols,
resolves types, checks expressions, specializes generic functions and closures,
builds IR and assembles bytecode. Text compilation can emit bytecode directly;
it does not require a PNG serialization and decoding roundtrip.

PNG input follows the PixelLang PNG and semantic pixel readers, reconstructs the
program AST, then uses the same checking and lowering stages. It never executes
hidden source text or embedded bytecode. Source recovery writes editable text
from that semantic representation. PNG export encodes checked program semantics.

The host service currently caches parser streams and selects changed function
bodies for checks using dependency interfaces. Artifact-producing builds check
all bodies; compatible function IR can then be reused across edits, with ID
relocation performed by PixelLang. Studio shares one checked compilation across bytecode, debug maps and
PNG; its IR panel loads and retains IR only when opened. See the [service contract](../reference/compiler-service.md)
for exact operations, limits, invalidation and cancellation.

## Runtime and editor

The Python VM executes compiler and user bytecode, including heap management and
general builtins. Debug metadata and generated semantic pixel maps connect code,
frames and Studio views. Recordings bind replay to the complete bytecode hash.

Formatting, incomplete-source navigation and optional spatial editing retain host
utilities. These are separate from the self-hosted check/build/recovery path.
The Python text/spatial compiler also remains for bootstrap and reference tests;
it is not the default application compilation route. See
[implementation boundaries](python.md) before removing modules.

## Source space and time

Executable source is two-dimensional. Studio's displayed Z axis represents logical
execution time, not a third source coordinate. IR and VM execution are independent
of display layout. Full 3D source remains a design direction, not current support.

## Verification

The [verification guide](../contributing/verification.md) distinguishes
related tests, merge checks and full release bootstrap. Validation must match the current source, runtime and compiler artifact.
