# Compiler service protocol, version 1

[中文](../../zh-CN/reference/compiler-service.md) · [English](compiler-service.md) · [Documentation](../README.md)

CLI and VS Code Studio share the supervised compiler service.

## Requests and results

A request carries `version: 1`, `id`, `revision`, `operation`, and operation inputs. `id` correlates the request; `revision` identifies the caller's source snapshot. Responses echo both and include the compiler artifact SHA-256. The editor suppresses results whose revision or captured source no longer matches its buffers.

| Operation | Input | Successful result |
| --- | --- | --- |
| check | files, entry | no artifact; structured diagnostics |
| compile | files, entry | bytecode |
| pixels | files, entry | semantic document |
| export-png | files, entry, scale | PNG bytes |
| compile-png | PNG bytes | bytecode recovered in PixelLang |
| recover-png | PNG bytes | editable source project (`files`, `entry`) recovered in PixelLang |
| module-info | files, entry | native parser module/import information |
| debug-bundle | files, entry, includeIR (default false), includePNG (default false), scale | bytecode, debug metadata and mapped document; optional IR and PNG |
| debug-compile | files, entry | bytecode and frame metadata |
| debug-pixels | files, entry | semantic document with source maps |
| export-debug-png | files, entry, scale | PNG with filenames/symbols; no source maps |
| inspect-ir | files, entry | native intermediate representation |

The in-process kernel uses bytes for PNG payloads. JSON-lines transport encodes them as `{"$bytes":"BASE64"}`; dictionaries containing reserved transport keys are escaped so program JSON values cannot be confused with binary transport values.

Responses have `status` (`ok`, `diagnostics`, `error`, `cancelled`, `timeout`), `diagnostics`, optional `result` or `error`, and `metrics`. Diagnostics retain the source module, phase, Unicode scalar offsets and one-based line/column. Cancellation and deadline errors use stable `service.cancelled` and `service.timeout` codes. Metrics record elapsed time, VM steps, parsed modules, parse hits and cache bytes.

Compiler operations do not execute the requested user program. Compiler output files live in isolated temporary directories and are returned only after success. Application execution and debugging are separate operations.

## Supervision, cancellation and limits

`python -m pixellang.compiler_rpc` (installed command `pixelcompiler`) owns a persistent compiler child process. `--compiler PATH` explicitly selects a development artifact; otherwise the installed compiler and manifest are mandatory. There is no implicit host compiler fallback.

One child is one sequential execution lane. Each request restores a trusted empty VM checkpoint. Parser, analysis and IR caches survive successful requests. Cancellation or deadline expiry terminates the child; the next request starts a fresh one. Cooperative VM checks are complemented by parent-process supervision, including runtime primitives. Only bounded control messages use the process pipe; bulk payloads use temporary files.

Default limits are 120 seconds, 500 million VM instructions, 64 million heap items, 2 million heap objects, and 64 million output/file bytes. The configurable limits have finite upper bounds. The one-million-character VM input bound and generic JSON budgets remain unchanged; typed parser data crosses this boundary in bounded chunks. The wire limit is 96 MB, with at most eight queued requests.

`--timeout` includes queue wait, process startup and execution. Studio's multi-phase build and restoration preserve one submission timestamp across all compiler phases. A cancellation request has its own ID, `operation: "cancel"`, and `target: ORIGINAL_ID`. Its acknowledgement is separate from the original request's cancelled response. IDs must be strings or integers. EOF drains submitted requests; SIGTERM/SIGINT cancel pending work and shut down the child.

Studio method calls use `operation: "studio"` plus `method` and `args`. Runtime `method: "cancel"` stops a PixelVM session. Transport `operation: "cancel"` cancels a queued/running request. Bridge tests cover both routes.

## Incremental source analysis

The service caches native parser streams by compiler artifact, module path and exact source content. Storage is bounded to 128 entries and 32 MB. Unchanged modules reuse their parsed tokens/nodes through bounded typed files. `incremental: false` selects ordinary uncached parsing and disables IR and exact artifact reuse.

Every check performs global binding and type resolution. Incremental checks select function bodies using changed sources and a reverse module dependency graph. Native parser information contains imports and normalized structural interface signatures. Signatures exclude function bodies and offsets, while conservatively including private declarations that exported types may reference. Interface changes invalidate transitive dependents; body-only edits recheck the changed module. Builds check all bodies before emission.

Failed global resolution cannot reuse old success. Diagnostics for unchanged modules are retained and deterministically sorted. Tests compare cached/uncached output and diagnostics, body edits, interface edits, transitive dependencies, removed files, normalized paths, cancellation and restart.

## CLI and Studio consumers

`CompilerClient` supplies bundled standard libraries, uses native import discovery and translates compiler diagnostics to application source positions. Workspace build/run/test, standalone text/PNG compile/run/debug, source pack/render, and PNG unpack use the service. Builds publish via unique temporary files after success, preserving old artifacts on failure.

Studio uses the same client for checking, building, IR inspection, PNG export/recovery and recording restoration. Document edits abort older checks; revision and source-snapshot guards prevent stale diagnostics/builds. Build and recovery progress support cancellation. DAP launch handles termination and source edits before compilation completes. Sessions are registered only after successful artifact creation.

Formatting, navigation and optional spatial editing retain host utility paths; they are outside the native check/build/recovery compilation path.

## Debugging and recovery

`debug-compile` emits bytecode plus native function/source identities, parameter/capture/local names and generic arguments. `debug-pixels` emits source maps from generated regions and AST ranges. `prepare_debug` joins these outputs without a host frontend, requires complete function/module coverage and maps instruction ranges to real semantic pixels. Normal execution bytecode semantics remain unchanged.

Native recordings include the complete normalized bytecode hash. Restoration recompiles the saved source snapshot and requires matching bytecode and semantic source. It cannot silently replay through another compiler. Tests cover generic/closure frames, cross-module breakpoints and pixel maps, reverse execution and corrupted recordings.

`image_source.pxl` validates the PNG, builds/checks the semantic AST and calls `source_writer.pxl` to recover editable multi-file source. It supports closures, generics, nominal types, patterns, collections and control flow. No stored complete source text is used. Optional metadata supplies validated filename/symbol hints; collisions and unsafe paths receive generated identities. Imports are relative to recovered paths, and native checking validates the recovered project.

Native PNG export uses container header 0.9 with bounded connection chunks; semantic pixels retain format 0.8. See [image format](formats/png.md). Generated-compiler regression includes source recovery fixtures and blocks host frontend/image-codec calls.

## Building and verification

```sh
.venv/bin/python scripts/build_service_compiler.py --seed VERIFIED_STAGE3.json --output NEW_ARTIFACT_DIRECTORY
.venv/bin/python -m pixellang.compiler_rpc --compiler NEW_ARTIFACT_DIRECTORY/compiler.json
```

The build records source/seed/artifact hashes and rejects changed inputs. This alone does not prove the three-generation fixed point or complete PNG roundtrip. [Layered verification](../contributing/verification.md) separates daily selected tests, merge checks, full release bootstrap, and package/real-editor acceptance. Small-project latency and memory budgets do not stand in for whole-compiler PNG timing.

## Shared Studio transaction and exact artifact reuse

`debug-bundle` checks a project once and shares the checked program and IR across
bytecode, frame metadata, mapped pixels and optional IR/PNG outputs. PNG generation
uses the mapped semantic document after removing source-map metadata; it retains
validated filenames and symbols. No second frontend invocation is needed. Studio
build requests PNG for the visible image and defers IR serialization until the
Compiler · IR panel is opened. Recording-only compilation omits both optional
outputs. Restore requests the image in the same compilation transaction.

Successful bundles are cached inside one service process by compiler hash, limits
and exact request inputs (excluding only correlation ID and revision). All files,
entry, optional outputs and scale participate. Storage is capped at 16 entries and
32 MB of serialized results. Callers receive isolated values; failures and cancelled
requests are never cached. `reuseArtifacts: false` bypasses artifact reuse while
retaining parser and IR reuse; `incremental: false` bypasses all three layers. Cache hits still honor
cancellation and deadlines and report `artifactCacheHit`, `artifactCacheBytes`, and
zero executed VM steps. Exact bundle reuse and cross-edit IR reuse are separate
layers; body-changing builds still check all bodies.

Studio `method: "inspect-ir"` takes a session ID, compiles that immutable source
snapshot on first inspection and retains its IR in the session. It does not run on
ordinary build, stepping or replay. The extension coalesces pending inspection
requests, cancels them on source edits and discards results for superseded sessions.
The webview requests IR only when its panel is open; failed inspection does not
start an automatic retry on every runtime state update. This on-demand operation
can repeat checking once when requested; it can reuse compatible lowered functions,
but does not retain a checked AST across requests.

## Cross-edit IR reuse

`compile`, `debug-compile`, `inspect-ir` and `debug-bundle` support `reuseIR`
(default `true`, boolean). The cache retains one successful snapshot in a service
process, capped at the smaller of 8 MB and one eighth of the file budget. It is
disabled below a 1 MB cache allowance. Worker termination discards it.

Source stamps include the exact module text and the interfaces of its transitive
dependencies. Binding, types, body checking, specialization and frame planning
still run before cached functions enter the new IR. PixelLang performs identity
matching and function/type/module relocation; Python only owns bounded storage
and transport. Noncanonical source paths use ordinary lowering.

Functions are read on demand. Payload bytes are retained when relocation leaves
the body unchanged; prior and output filenames cannot collide when IDs change.
Oversized functions are omitted from the cache, and publication is atomic after
a successful response and final cancellation/deadline checks. Diagnostics and
failed/cancelled requests never replace the previous snapshot.

Metrics expose `irCacheHits` (reused functions), `irCacheRelocated` (reused functions
processed under changed ID maps), and `irCacheBytes` (retained serialized bytes).
These are separate from parser and complete bundle hits. `reuseIR: false` skips
this layer without disabling parsing reuse; it does not erase the retained cache.
An exact bundle hit executes no IR work and reports zero IR hits for that request.

Cache transport and initial population have costs: hit counts do not imply a
latency improvement for every edit. Compare with `reuseArtifacts: false` in both
variants and toggle only `reuseIR`. See [performance measurement](../contributing/performance.md)
and [IR reuse](../internals/ir.md)../../design/adr/0010-incremental-ir.md).
