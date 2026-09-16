# Conformance coverage

[中文](../../zh-CN/contributing/conformance.md) · [English](conformance.md) · [Documentation](../README.md)

Use [layered gates](verification.md) for the change's scope. Ordinary unittest discover
does not replace bootstrap, generated-compiler, image, installation or real-editor checks.

| Contract | Validation direction |
| --- | --- |
| Executable color/spatial semantics | Color edits, operand positions, indentation, next/child, invalid alpha and disconnected regions |
| No OCR or hidden executable text | Real text-free PNGs, removed ancillary metadata, independent image execution |
| AST and canonicalization | Inspectable regions/provenance, meaning preservation and idempotence |
| IR and VM | Deterministic bytecode, optimizer agreement, random arithmetic, calls/recursion/loops, invalid executables |
| Types and data | Numeric boundaries, generics, closures, aliases, enums, errors, JSON, module visibility/cycles |
| Bootstrap and recovery | Complete equal generations from identical sources; generated/recovered compilers run real multifile projects |
| Debugging and effects | Maps, snapshots, GC, every-state replay, cancellation, grants and no repeated writes |
| Caches and service | Differential modes, invalidation, relocation, no publication on failure, deadlines, restarts, stale results |
| Distribution and tooling | Isolated wheels, source-free images, Node Bridge, DAP, real VS Code interaction |

Arbitrary-dimensional point structures describe an extension boundary, not 3D source
support. Test existence does not prove the current artifact passed: run checks and
verify source/artifact identities.
