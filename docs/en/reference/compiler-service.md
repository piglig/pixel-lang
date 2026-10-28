# Compiler service protocol

[简体中文](../../zh-CN/reference/compiler-service.md) · [English](compiler-service.md) · [Documentation](../README.md)

The protocol version is 1. CLI and Studio share request, cancellation and snapshot contracts. This reference is for service clients and tool authors.


**On this page**

- [Requests and results](#requests-and-results)
- [Supervision, cancellation and limits](#supervision-cancellation-and-limits)
- [Incremental source analysis](#incremental-source-analysis)
- [CLI and Studio consumers](#cli-and-studio-consumers)
- [Debugging and recovery](#debugging-and-recovery)
- [Building and verification](#building-and-verification)
- [Shared Studio transaction and exact artifact reuse](#shared-studio-transaction-and-exact-artifact-reuse)
- [Resident IR reuse](#resident-ir-reuse)
- [Native editor queries](#native-editor-queries)
- [Further reading](#further-reading)

## Requests and results

A request carries `version: 1`, `id`, `revision`, `operation`, and operation inputs. `id` correlates the request; `revision` identifies the caller's source snapshot. Responses echo both and include the compiler artifact SHA-256. The editor suppresses results whose revision or captured source no longer matches its buffers.

| Operation | Input | Successful result |
| --- | --- | --- |
| check | files, entry | no artifact; structured diagnostics |
| compile | files, entry, optional entryFunction | bytecode |
| pixels | files, entry | semantic document |
| export-png | files, entry, scale | PNG bytes |
| compile-png | PNG bytes | bytecode recovered in PixelLang |
| compile-pixels | document, optional entry (diagnostic name) | bytecode decoded and compiled from a semantic pixel document in PixelLang |
| recover-pixels | document, optional entry (diagnostic name) | editable source project (`files`, `entry`) |
| export-pixel-png | document, scale | PNG bytes after native validation |
| normalize-pixels | document | canonical semantic document after native type checking |
| recover-png | PNG bytes | editable source project (`files`, `entry`) recovered in PixelLang |
| module-info | files, entry | imports, tests (`name`, `valid`), hasMain |
| format | files, entry | native syntax formatting; returns text without resolving imports |
| debug-bundle | files, entry, includeIR (default false), includePNG (default false), scale | bytecode, debug metadata and mapped document; optional IR and PNG |
| debug-compile | files, entry | bytecode and frame metadata |
| debug-pixels | files, entry | semantic document with source maps |
| export-debug-png | files, entry, scale | PNG with filenames/symbols; no source maps |
| inspect-ir | files, entry | native intermediate representation |
| native-graphs | files, entry | projection of native graphs, ports, coordinate expressions and source ranges |
| editor-index | files, entry, optional editorRoots | native editor index, with diagnostics for partial source |

The in-process kernel uses bytes for PNG payloads. JSON-lines transport encodes them as `{"$bytes":"BASE64"}`; dictionaries containing reserved transport keys are escaped so program JSON values cannot be confused with binary transport values.

Responses have `status` (`ok`, `diagnostics`, `error`, `cancelled`, `timeout`), `diagnostics`, optional `result` or `error`, and `metrics`. Diagnostics retain the source module, phase, Unicode scalar offsets and one-based line/column. Cancellation and deadline errors use stable `service.cancelled` and `service.timeout` codes. Metrics record elapsed time, VM steps, parsed modules, parse hits, resident cache nodes and exact-artifact cache bytes.

Compiler operations do not execute the requested user program. Compiler output files live in isolated temporary directories and are returned only after success. Application execution and debugging are separate operations.

`compile.entryFunction` selects a top-level ordinary function in the entry module;
omitting it selects `main`. The selected function must be non-generic, parameterless
and return `unit`. The compiler selects it during instantiation without adding source
text. Other service operations reject this option. `module-info` discovers top-level
`fn test_*` declarations, including exported functions; nested functions are excluded.
Its signature flag is syntactic, and compilation still performs full type checking.

## Supervision, cancellation and limits

`python -m pixellang.compiler_rpc` (installed command `pixelcompiler`) owns a persistent compiler child process. `--compiler PATH` explicitly selects a development artifact; otherwise the installed compiler and manifest are mandatory. There is no implicit host compiler fallback.

One child is one sequential execution lane. Reference VM requests restore a trusted empty checkpoint. Native sessions own parsing, semantic and IR caches across successful requests. Running-request cancellation first signals the VM and allows at most 100 ms, clipped to the existing request deadline, for cleanup and acknowledgement. A responsive supervisor child remains available; interrupted native sessions discard their compiler caches; its cancelled reply is discarded even if it reports success. Startup cancellation, deadline expiry or an unresponsive child still terminates the process; the next request starts a fresh one. Parent-process supervision therefore still covers runtime primitives. Only bounded control messages use the process pipe; bulk payloads use temporary files.

Default limits are 120 seconds, 500 million VM instructions, 64 million heap items, 2 million heap objects, and 64 million output/file bytes. The configurable limits have finite upper bounds. The one-million-character VM input bound and generic JSON budgets remain unchanged; typed parser data crosses this boundary in bounded chunks. The wire limit is 96 MB, with at most eight queued requests.

`--timeout` includes queue wait, process startup and execution. Studio's multi-phase build and restoration preserve one submission timestamp across all compiler phases. A cancellation request has its own ID, `operation: "cancel"`, and `target: ORIGINAL_ID`. Its acknowledgement is separate from the original request's cancelled response. IDs must be strings or integers. EOF drains submitted requests; SIGTERM/SIGINT cancel pending work and shut down the child.

Studio method calls use `operation: "studio"` plus `method` and `args`. Runtime `method: "cancel"` stops a PixelVM session. Transport `operation: "cancel"` cancels a queued/running request. Bridge tests cover both routes.

## Incremental source analysis

Incremental parsing, function checks and IR reuse belong to the native compiler session.
The Python service does not retain parsed modules, calculate semantic invalidation or merge
previous diagnostics. Reference VM and non-resident executions check the complete input on
each request, providing an independent fresh-compilation comparison.
`incremental: false` disables session reuse and exact artifact reuse. Resident services execute such requests in a temporary native process, closed on success or failure, preserving the warm process and its snapshots. This keeps retained caches out of the full compilation process budget; both processes may coexist, so total host memory can exceed one process budget. Module declaration tables are still rebuilt globally. Declaration-body binding queries
reuse observed name lookups, relocating local scopes, references and closure captures.
New shadowing declarations, visibility changes and import retargeting invalidate affected queries.
Declaration type results are reused with relocated type and syntax identities; constraints are checked after all schemas are restored. Successful unchanged function queries
and their IR can be reused using semantic dependencies and frame contracts. Flow semantic queries
retain typed plans, initializers, child signatures and expression results. Stable identities relocate
nodes, symbols and types on reuse; changed dependency contracts invalidate the query.
Failed checks never publish a partial semantic snapshot.
Tests compare results and diagnostics with fresh compilation across edits, transitive
dependencies, deletion, path normalization, cancellation and process restart.

## CLI and Studio consumers

`CompilerClient` supplies bundled standard libraries, uses native import discovery and translates compiler diagnostics to application source positions. Workspace build/run/test, standalone text/PNG compile/run/debug, source pack/render, and PNG unpack use the service. Builds publish via unique temporary files after success, preserving old artifacts on failure.

Studio uses the same client for checking, building, IR inspection, PNG export/recovery and recording restoration. Document edits abort older checks; revision and source-snapshot guards prevent stale diagnostics/builds. Build and recovery progress support cancellation. DAP launch handles termination and source edits before compilation completes. Sessions are registered only after successful artifact creation.

Formatting and language navigation consume native compiler tokens, AST, scopes, bindings and types. The host projects editor results and text edits without reparsing or inferring language types. Native graph and coordinate edits use native AST ranges as well; the original 2D semantic editor remains a separate entry point.

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
validated filenames and symbols. This bulk operation is used for explicit full-artifact
inspection. Studio build and recording restore use `debug-compile`: executable text
provenance and frame metadata do not depend on pixel layout. `debug-pixels` is requested
when the execution-space view is opened, `export-debug-png` for image viewing/export,
and `inspect-ir` when the IR panel is opened. Projection is separate from bytecode and
does not change the recording hash or execution history. Timeline format 1.0 stores
the source snapshot and executable identity, not the generated pixel document.

Successful text-debug artifacts and bundles are cached inside one service process by compiler hash, limits
and exact request inputs (excluding only correlation ID and revision). All files,
entry, optional outputs and scale participate. Storage is capped at 16 entries and
32 MB of serialized results. Callers receive isolated values; failures and cancelled
requests are never cached. `reuseArtifacts: false` bypasses artifact reuse while
retaining parser and IR reuse; `incremental: false` bypasses all three layers. Cache hits still honor
cancellation and deadlines and report `artifactCacheHit`, `artifactCacheBytes`, and
zero executed VM steps. Exact bundle reuse and cross-edit IR reuse are separate
layers; body-changing builds use native queries to recheck affected functions.

Studio `method: "inspect-ir"` takes a session ID, compiles that immutable source
snapshot on first inspection and retains its IR in the session. It does not run on
ordinary build, stepping or replay. The extension coalesces pending inspection
requests, cancels them on source edits and discards results for superseded sessions.
The webview requests IR only when its panel is open; failed inspection does not
start an automatic retry on every runtime state update. This on-demand operation
can repeat checking once when requested; it can reuse compatible lowered functions,
but does not retain a checked AST across requests.

## Resident IR reuse

The development compiler retains lowered functions as native values in its resident
session. `incremental` (default `true`) controls syntax, semantic and IR reuse;
`incremental: false` runs fresh compilation without replacing retained snapshots.
Per-stage cache switches have been removed.

The compiler derives source stamps from exact token values and locations plus the
contracts of transitive imports. Ordinary imported function bodies are excluded
from interface stamps; flow declarations remain conservative. Ordinary functions reuse the checked function fingerprint, observed contracts and concrete frame layout. Function bodies and dependency contracts are retained as structural values and compared by content, without JSON serialization. Structural entries count toward the semantic, frame and IR retention budgets; exceeding capacity discards acceleration without changing compilation results. Candidate acceptance covers self-hosting, PNG/text recovery and incremental/fresh equivalence. Stable declaration/child paths relocate instruction nodes and current source spans, including closures. Flow semantic queries can be reused independently. Flow IR, initializer functions and graph metadata are cached together, relocating nodes, nominal types, call targets and generated function names as one unit. Unchanged identity layouts and declaration token positions allow direct reuse. Ordinary functions without a retained semantic query use conservative module invalidation. Binding and declaration type results are reused by declaration and relocated to current identities. Frame planning is also cached by declaration/closure identity and generic arguments. Body or observed-contract changes trigger replanning; reuse relocates symbols and concrete types instead of retaining old arena IDs. Frame results publish and evict with successful IR snapshots and count toward their retention budget. Contracts are shared per snapshot rather than copied into every function entry. Temporary frame indexes are released before IR construction. The bundled development compiler includes frame queries. Targeted tests, generated and recovered compiler regressions, full-source incremental/fresh comparisons, full PNG recovery and isolated installation have been verified. Complete release acceptance still requires the layered gates. Global declaration indexing, type-constraint validation and specialization still execute. This is not complete
function-level incremental compilation.

Snapshots contain functions and identity maps directly: no IR index or function
payload files are serialized, and no host parser dependency graph is required.
Type, module and call-target identities relocate before reuse; string constants
remain opaque. Failed lowering does not replace the previous IR snapshot.
Cancellation, execution failure or invalid session statistics dispose of the
process and all its cached state. Capacity eviction discards acceleration only.

`irCacheHits` counts reused functions, `irCacheRelocated` counts reuse under
changed identity maps, and `residentIRNodes` counts retained IR structures.
The node metric is not a byte measurement. The combined syntax/semantic and IR
retention allowance is two million counted structures. Exact artifact hits do
not execute IR work. Compare fresh and resident requests with
`reuseArtifacts: false`; hit counts alone do not establish a latency improvement.

### Declaration query statistics

Response `metrics` reports per-request query counts:

| Field | Meaning |
| --- | --- |
| `bindingCacheHits` | Declarations whose binding results were reused |
| `bindingCheckedDeclarations` | Declarations rebound in this request |
| `typeCacheHits` | Declarations whose type analysis results were reused |
| `typeCheckedDeclarations` | Declarations whose types were reanalyzed |

These counters measure resident incremental queries and reset for every request.
Exact artifact hits, failures before queries, and the non-incremental analysis path
selected by `incremental: false` report zero; zero does not mean no other compiler
work occurred. Counts refer to declarations, including functions, records and enums,
not files. They do not replace latency or memory measurements.

## Native editor queries

`native-graphs` returns `result.projection`, version `pixellang-native-graphs/2`.
It retains inputs, named expressions, persistent child instances, states, next/output
expressions, bound dependencies and coordinate expressions. This is a derived view of current source, not another program store.
Diagnostics may accompany a partial projection; it is not an executable artifact.

`editor-index` returns `result.index`, containing native syntax nodes, lexical scopes,
symbol references, types, call signatures, expressions and flow information. Incomplete
source can return `status: ok` with diagnostics for completion and navigation. Strict
`check` and `compile` still reject incomplete programs. `editorRoots` lists at most
128 paths supplied in `files`, defaulting to all supplied paths. The client records
user roots before adding the standard library, so unused library modules need not
enter editor analysis. User modules disconnected from the entry can still be indexed;
the native module resolver determines their dependencies.

The development VM 0.9 index separates declaration schemas in `resolved.types` from
receiver permissions in `memberViews`. The latter maps decimal type IDs to `storage`,
`fields` and `variants`; `storage` identifies the nominal declaration, while member
payload types are projected by the native checker. Use these views for member lookup:
readonly records cannot expose declared mutable fields as writable. Callable field
signatures remain unchanged. Missing views mean unavailable member information, not
permission to reconstruct a schema in the client. The publication pass is bounded to
known types rather than recursively expanding potentially infinite generic schemas.

These structures use module/node/type identities within one compiler artifact.
Callers must not reuse isolated node identities across compilation requests: verify
the complete source snapshot and revision. The workstation uses the index for
completion, signatures, definitions, references, rename, outline, hover and formatting.
Rename reanalyzes the proposed source and verifies reference bindings, rejecting name
capture and standard-library writes.

## Further reading

- [Documentation index](../README.md)
