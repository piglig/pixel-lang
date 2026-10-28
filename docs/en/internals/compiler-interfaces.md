# Compiler interfaces

[简体中文](../../zh-CN/internals/compiler-interfaces.md) · [English](compiler-interfaces.md) · [Documentation](../README.md)

This page describes data contracts between compiler stages. Interface changes must update producers, consumers, provenance and differential checks together.


**On this page**

- [Contract and interface](#contract-and-interface)
- [Sources, syntax and identity](#sources-syntax-and-identity)
- [Types, instances and frames](#types-instances-and-frames)
- [IR, control flow and assembly](#ir-control-flow-and-assembly)
- [Pixel and image path](#pixel-and-image-path)
- [Shared executable code](#shared-executable-code)
- [Compiler transport and reuse](#compiler-transport-and-reuse)
- [Resident session core](#resident-session-core)
- [Further reading](#further-reading)

## Contract and interface

These are implementation contracts; language rules live in the [reference](../reference/language/overview.md).
Field definitions are authoritative in [model.pxl](../../../selfhost/model.pxl),
[ir_state.pxl](../../../selfhost/ir_state.pxl) and each module's exported types.

## Sources, syntax and identity

The driver supplies relative POSIX `.pxl` paths mapped to text, including std/ sources.
The resolver performs no hidden host filesystem lookup. Relative imports resolve
from the importing module; std/ resolves from the mapping root. Reject root escapes,
absolute paths, backslashes, NUL, duplicate normalized paths and more than 128 files.
project.Build emits dependency-first modules with parsed arenas, import targets/aliases/nodes
and declaration names/kinds/nodes/visibility. Cycles, missing sources, duplicates and
invalid module statements produce diagnostics; partial modules are not successful output.

`Project.entryName` identifies the top-level entry declaration in the final module.
Text projects select `main`; decoded pixel projects retain the declaration marked
as entry. Instantiation uses that identity directly, without inserting an AST wrapper.
Pixel normalization therefore does not accumulate entry functions. Source recovery
exposes a textual `main` that calls the preserved pixel entry name, so lexical
shadowing and references remain intact at the editable-source boundary.

Node indices are module-local, children precede parents and parent spans contain
children. Token offsets count Unicode scalars. Cross-module identity combines module
and local node IDs, not names alone. Scope.parent links lexical scopes; owner is a
function node (-1 for module scope). Symbol retains declaration, scope, type and
visibility; mutable marks a writable binding, including nested local value updates. Reference records resolved uses; Capture
records a function and outer Symbol, deduplicated in deterministic order. Types and
aliases obey the same visibility rules.

## Types, instances and frames

Project-wide interned TypeInfo covers primitive, array, map, function, record, enum
and type_parameter. Arguments contain container values, nominal arguments or callable
parameters followed by the result. Nominal identity includes declaration module/node.
Fields and variant payloads retain type identity; templates retain open types and
instances retain declarations/substitutions. Unresolved -1 types cannot reach emission.

The chain is BoundProject → ResolvedTypes → ExpressionCheck → InstancePlan → ClosurePlan.
`ResolvedTypes` is the authoritative owner of interned type data. The pass-local
`ResolutionIndex` holds readonly symbol lookups without duplicating the type arena.
`types.Prepare` initializes the name environment and primitive types;
`types.ResolveDeclaration` resolves one declaration's annotations, signatures and
explicit bindings; `types.Finish` checks constraints after all nominal schemas exist.
`types.Analyze` composes these stages for a complete analysis. Resident `type_queries` caches declaration results using stable syntax origins and
private type descriptors. Unchanged files skip structural comparison; edited declarations
and changed type-name targets are resolved again. Nominal constraint validation still runs
after restoring all schemas, including annotations in unused declarations. Failed requests
never publish the new snapshot. Declaration and expression queries share a source identity
index; reverse locations are allocated only for identities retained by previous queries.
Specialization and global constraint validation retain project-wide work; frame planning has its own declaration queries. Substitution and queries use the same arena directly.
`inference.Context` contains only parameters, candidate arguments, conflicts, and
deferred projections, with ordinary value semantics. Constraint collection uses
explicit `inout`; copied contexts can infer independently. Preview/Complete read
the context without modifying it. The caller supplies the arena separately, and
newly instantiated types remain in that arena. Arena, checker and IR state all follow value semantics; explicit access grants writeback.

schemas.Materialize traverses concrete roots, structural arguments, fields and payloads.
A visited set handles recursion; first discovery determines schema order. Substitute
generic fields before traversal; Option/Error use the same path. Limits are depth 32
and 256 nominal schemas. schemas.Build seeds reachable signatures, concrete locals/
expressions, closure signatures and captures. Open template names are not runtime roots;
even unused local slots contribute concrete roots.

frames.Build assigns named-instance IDs before closure IDs; FramePlan.entry preserves
the planned entry. Frames retain provenance, concrete signature, owner instance and
closure index. Slots hold parameters, captures, then locals, distinguished by Symbol
IDs even under shadowing. Captures preserve source Symbols; all types are concrete and
rebinding mutability is independent of slot category. Layout allocates no runtime cells:
IR emits CAPTURE_VALUE/CLOSURE and fresh BIND on declaration/iteration entry. Lowering appends
temporaries for snapshots, cursors and match subjects, within 65,536 total slots.

## IR, control flow and assembly

ir_state Instruction holds op, typed JSON argument, module/node and provenance;
Function holds signatures, slot count and instructions; Program holds versions,
schemas, entry and functions. Nominal spelling is @module:declaration with concrete
arguments; the runtime string name is str. See [IR](ir.md): preserve provenance,
concrete types and symbolic constants; diagnostics make output non-executable.
for evaluates once using single/pair ITER_SNAPSHOT; continue advances and loop transfers
release accesses exited by the transfer. match stores its subject once, uses ENUM_IS/ENUM_GET and
fresh arm bindings, and traps on an unmatched runtime subject. RESULT_DECODE carries the
substituted target type.

assembler.Assemble resolves per-function LABELs against non-label instruction positions
and interns CONST by type and canonical JSON spelling, distinguishing signed float zero.
Duplicate/unknown labels and out-of-range targets produce source diagnostics. Assembly
does not replace VM schema/opcode validation. Bytecode streams transport the complete
artifact in chunks, not summaries. Driver operations are defined in main.pxl; the public
service contract is [documented separately](../reference/compiler-service.md).

## Pixel and image path

| Modules | Responsibility |
| --- | --- |
| literal_pixels, pixel_tokens | Literal bytes, versioned tables, bounded identifiers |
| pixel_ast_state, pixel_ast_expr, pixel_ast | Emit regions, names and modules from checked types/bindings |
| spatial_layout, picture_layout | Logical/physical layout, ordering, parents and size limits |
| pixel_decode, pixel_regions, pixel_reader, pixel_expressions, pixel_statements, pixel_types | Validate colors/regions/types/syntax and rebuild nodes with provenance |
| pixel_restore | Restore each detached lambda_body once at its lexical reference; reject missing/duplicate/cyclic/unreferenced bodies and rebuild a postorder arena |
| pixel_project, pixel_document | Validate module IDs, bundles, schemas, cycles and entries; connect pixels to common checking |
| png, picture, picture_reader | PNG bytes, physical headers/layout, palette and module recovery |
| image_transport | Bounded module and bulk transport |
| image_source, source_writer | Recover editable source from checked images |

Pixel project root ID is 0; dependency keys are canonical decimal IDs 1..65535, with
at most 127 dependencies. Entry identity is explicit, never a global name replacement.
Provenance retains exact spatial points rather than treating them as text offsets.
Native .pixel may omit metadata/links and use empty defaults; explicit null is invalid,
and bundles must have a supported version. Metadata carries no executable source, AST
or bytecode. See [PNG](../reference/formats/png.md) for bounded connections and layout.

PNG writing emits 8-bit RGBA without ancillary chunks. Reading supports non-interlaced
8-bit RGB/RGBA and all five filters, validating signature, chunk types/reserved bits,
CRC, order, consecutive IDAT, IEND and sizes. Unknown critical chunks, duplicate palettes
and unsupported tRNS/color/interlace fail. Palette, grayscale, 16-bit and Adam7 are
unsupported; ancillary chunks are CRC-checked then ignored. crc32/zlibCompress/
zlibDecompress are general primitives; decompression has an explicit output limit and
rejects corrupt, truncated, trailing and concatenated streams. readBytes/writeBytes
use granted roots, relative paths, no-follow access and atomic replacement, with a
64-million-byte file ceiling. Effects verify request digests, read limits and written
bytes; replay never accesses the filesystem. Heap, step and effect budgets still apply.

`zlibCompress(bytes)` synchronously reads an integer array and returns
`Result[[int], Error]` containing independent compressed bytes. Invalid byte values
produce `Err` with code `bytes.range`, leaving the input unchanged. The PNG encoder
uses this checked boundary instead of scanning the whole canvas a second time.
`zlibDecompress(bytes, limit)` also returns `Result[[int], Error]`;
the explicit typed form is `zlibDecompress::[[int]](bytes, limit)`. Array values use
the same `let`/`var` and `inout` rules as other containers. There is no mutable-type
wrapper or separate conversion between frozen and mutable arrays.
Validation completes before a successful value is published. Picture recovery uses
`DecodeRaster`, retaining row markers and unfiltering in place; filter-zero rows
require no pixel copying. The ordinary `Decode` API materializes RGBA data and
therefore needs additional working space. Current artifact and whole-compiler
Acceptance reports bind exact source and artifact fingerprints; see [verification](../contributing/verification.md).

## Shared executable code

The host `CodeImage` detaches bytecode into a read-only object graph and validates it
once. VMs can share that graph while retaining independent heaps, stacks, inputs,
outputs, flow state and execution cursors. Changes to the caller's original dictionary
do not affect prepared code. Historical replay and single-event debugging reuse the
image instead of copying and validating all instructions and debug metadata for each
new VM. Passing a dictionary directly to a VM still performs isolation and validation.

This is an internal host resource, not a PixelLang immutable data type or a security
boundary against hostile Python code. Measure preparation and execution separately.

Native executable source maps use `pixellang-native-source-map/2`. Each native function ID indexes a `{name, instructionCount}` entry; its PC indexes that function in the SHA-256-bound compiler bytecode. Instructions and source spans are read from that bytecode, not duplicated in the map. Loaders reject earlier maps and inconsistent function identities or lengths. This keeps startup from allocating a second complete instruction graph.

## Compiler transport and reuse

Bytecode stream protocol 2 emits each source span once, keyed internally by its
module/node identity. Instructions carry an integer reference to a previously emitted
span; the end record counts spans as well as instructions and declarations. The host
rejects missing, out-of-order or invalid references, restores the complete bytecode
and validates it. Source provenance remains shared through debug-image construction.
Earlier stream protocols are not accepted.

The supervised worker uses a private binary response file to preserve bytes and shared
objects. Its restricted pickle reader rejects global/class resolution, persistent
references and trailing data. This is transient internal IPC, not an external artifact
format; public compiler-service and Studio messages remain JSON. Output limits,
cancellation, deadlines and worker termination remain enforced.

Parsed-module retention is owned by the native session. The service no longer stores
encoded parse chunks or stages them into temporary files for reference execution.
Reference VM requests consume complete source input. Lowering still builds the function
signature parameter index once per project.

## Resident session core

`selfhost/compiler_session.pxl` owns parsed modules and binding, declaration-type, function-semantic,
frame and IR query snapshots. Successful requests publish snapshots; diagnostics retain the last
successful state and capacity eviction affects acceleration only. Stable declaration/child paths
relocate symbols, types, instructions and source spans into the current project. Flow IR, initializers
and graph metadata reuse together. Global indexes, constraints and specialization retain project-wide
phases. The [compiler service](../reference/compiler-service.md) defines dependencies, retention,
invalidation and statistics.

### Native session transport

`build_native(..., session={initialize, advance})` binds two bytecode functions: initialization returns
an owned state value, and advancement accepts and returns that type. The source map validates the
contract. `NativeSessionExecution` frames requests with lengths, identities and independent budgets.
Successful responses publish state; cancellation, timeout and execution faults reap the native process,
so the next request initializes a new session. CompilerService selects the declared executor and the
worker closes it on exit.

Semantic and IR snapshots use neither disk serialization nor duplicate host dependency graphs.
`incremental: false` provides fresh-compilation comparisons. Tests compare diagnostics and bytecode
across successive edits, contract changes, failure recovery, nominal/source relocation and capacity
eviction. See [verification](../contributing/verification.md) for installation and performance requirements.

## Further reading

- [Documentation index](../README.md)
- [Compiler architecture](architecture.md)
