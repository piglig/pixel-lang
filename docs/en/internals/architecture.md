# Compiler architecture

[简体中文](../../zh-CN/internals/architecture.md) · [English](architecture.md) · [Documentation](../README.md)

CLI and Studio share the PixelLang compiler. The host owns processes, transport and tool integration; self-hosted source defines language behavior.

## Compilation path

```text
CLI / VS Code Studio
        ↓
CompilerClient → CompilerWorker → CompilerService
                                      ↓
                          Native PixelLang compiler
                                      ↓
                       Parse → Bind → Type → Specialize
                                      ↓
                         Frames → IR → Bytecode
```

Text parses directly from `.pxl`. Image compilation reconstructs modules and syntax from exact pixels before entering the same checking and lowering pipeline. Native executable, bytecode, source map and manifest must match. Ordinary requests neither bootstrap again nor fall back to the old Python frontend.

## Sessions and incrementality

Resident native sessions retain parsing, binding, declaration-type, function-semantic, frame and IR queries. Stable declaration identities and semantic dependencies determine reuse; source ranges and runtime identities relocate into the current project. Failed requests do not publish partial state, and eviction affects performance only. Global declaration indexing, constraint validation and specialization still have project-wide phases. See the [compiler service](../reference/compiler-service.md) for fields and invalidation rules.

## Execution and tools

Python PixelVM executes application bytecode and provides debugging, file permissions, checkpoints and replay. The native runtime also serves compiler execution and differential verification. Editor syntax, types and references come from the native compiler. The host converts Unicode scalar ranges into VS Code edits and validates source snapshots before applying them. See [Python responsibilities](python.md).

## Representation boundaries

| Representation | Responsibility |
| --- | --- |
| Text / semantic pixels | Editable, recoverable program source |
| AST / typed plan | Declarations, bindings, types, space and state |
| IR / bytecode | Control flow, value operations, source locations and entry |
| Runtime state / recording | Input, heap, instances, checkpoints and recorded effects |
| Studio views | Source projections, spatial canvas and history navigation |

Spatial XYZ and logical time are independent. A two-dimensional PNG container does not limit the program's native 3D semantics. See [compiler interfaces](compiler-interfaces.md) and [conformance](../contributing/conformance.md).
