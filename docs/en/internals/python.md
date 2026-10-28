# Python responsibilities

[简体中文](../../zh-CN/internals/python.md) · [English](python.md) · [Documentation](../README.md)

Self-hosted compiler and host runtime have separate responsibilities. Removing historical frontends does not remove Python execution, debugging or engineering tools.

## Contract and interface

Compiler self-hosting and removing Python from the runtime are separate goals.
`selfhost/*.pxl` implements lexing, parsing, binding, types, specialization, IR,
assembly and image recovery. CLI/Studio compile through the bundled native compiler,
validating its binding to the compiler bytecode. They neither rebuild it per request
nor silently fall back to the reference frontend. Python PixelVM remains available
for program execution, debugging and differential verification.

| Layer | Main locations | Responsibility |
| --- | --- | --- |
| Self-hosted compiler | `selfhost/` | Language and image compilation rules |
| Runtime | `pixellang/vm.py`, `fileaccess.py`, `jsonvalue.py` | Instructions, memory, general primitives, I/O |
| Service | `compiler_service.py`, `compiler_worker.py`, `compiler_rpc.py`, `compiler_client.py` | Validation, caches, processes, cancellation, budgets, clients |
| Debugging | `recording.py`, `temporal.py`, `debug_artifact.py` | State, source maps, replay, consistency |
| Reference parsing | `text.py`, `parser.py`, `spatial.py` | Independent parsing and spatial structure checks; excluded from CLI/Studio compilation |
| Engineering | `scripts/`, `tests/`, `bootstrap.py`, `regression.py` | Builds, tests, measurements |

Unqualified module names are under pixellang. Physical packages do not yet mirror
these boundaries. The native compiler handles incomplete source, symbol indexing
and formatting for Studio. `native_editor.py` exposes the index to the workstation;
`spatialedit.py` uses native ASTs to generate source edits. The old Python editor
implementation has been removed, along with the old Python project compiler,
semantic checker, IR lowering, assembler and canonical frontend. Self-hosting uses
the bundled compiler seed. Remaining reference parsers provide independent
component checks; they must not serve as fallback compilation or editing paths.
Runtime and service Python dependencies are currently required; engineering scripts
may remain Python permanently.

`native_codegen.py` produces the native compiler; the corresponding `native_*.h`
files implement values, access, JSON and file operations. Native execution and
Python VM execution must agree on numbers, strings, value isolation, errors and
budgets. Python VM debugging and replay remain supported; removing an old
compilation experiment does not remove those runtime capabilities.

## Further reading

- [Documentation index](../README.md)
- [Compiler architecture](architecture.md)
