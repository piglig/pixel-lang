# Python responsibilities and implementation boundaries

[中文](../../zh-CN/internals/python.md) · [English](python.md) · [Documentation](../README.md)

Compiler self-hosting and removing Python from the runtime are separate goals.
`selfhost/*.pxl` implements lexing, parsing, binding, types, specialization, IR,
assembly and image recovery. Python PixelVM executes the pinned compiler bytecode.
CLI/Studio neither rebuild it per request nor silently fall back to the reference frontend.
“Self-hosted frontend” does not mean machine-code execution.

| Layer | Main locations | Responsibility |
| --- | --- | --- |
| Self-hosted compiler | `selfhost/` | Language and image compilation rules |
| Runtime | `pixellang/vm.py`, `fileaccess.py`, `jsonvalue.py` | Instructions, memory, general primitives, I/O |
| Service | `compiler_service.py`, `compiler_worker.py`, `compiler_rpc.py`, `compiler_client.py` | Validation, caches, processes, cancellation, budgets, clients |
| Debugging | `recording.py`, `temporal.py`, `debug_artifact.py` | State, source maps, replay, consistency |
| Bootstrap/reference | `text.py`, `parser.py`, `semantics.py`, `backend.py`, `project.py` | Initial seed, differential checks, some editor utilities |
| Engineering | `scripts/`, `tests/`, `bootstrap.py`, `regression.py` | Builds, tests, measurements |

Unqualified module names are under pixellang. Physical packages do not yet mirror
these boundaries. The reference frontend cannot simply be deleted: editor.py still
handles incomplete source, and spatial editing and formatting retain host callers.
Runtime and service Python dependencies are currently required; engineering scripts
may remain Python permanently.

A native VM must validate numbers, strings, aliases, errors, budgets, debugging and
replay against identical bytecode. Replacing dispatch does not remove Python from
the package: service and tool hosting would also need migration.
See the [native VM prototype](native-vm.md).
