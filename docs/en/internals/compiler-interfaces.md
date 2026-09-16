# Self-hosted compiler interfaces

[中文](../../zh-CN/internals/compiler-interfaces.md) · [English](compiler-interfaces.md) · [Documentation](../README.md)

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

Node indices are module-local, children precede parents and parent spans contain
children. Token offsets count Unicode scalars. Cross-module identity combines module
and local node IDs, not names alone. Scope.parent links lexical scopes; owner is a
function node (-1 for module scope). Symbol retains declaration, scope, type and
visibility; mutable permits rebinding only. Reference records resolved uses; Capture
records a function and outer Symbol, deduplicated in deterministic order. Types and
aliases obey the same visibility rules.

## Types, instances and frames

Project-wide interned TypeInfo covers primitive, array, map, function, record, enum
and type_parameter. Arguments contain container values, nominal arguments or callable
parameters followed by the result. Nominal identity includes declaration module/node.
Fields and variant payloads retain type identity; templates retain open types and
instances retain declarations/substitutions. Unresolved -1 types cannot reach emission.

The chain is BoundProject → ResolvedTypes → ExpressionCheck → InstancePlan → ClosurePlan.
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
IR emits CAPTURE/CLOSURE and fresh BIND on declaration/iteration entry. Lowering appends
temporaries for snapshots, cursors and match subjects, within 65,536 total slots.

## IR, control flow and assembly

ir_state Instruction holds op, typed JSON argument, module/node and provenance;
Function holds signatures, slot count and instructions; Program holds versions,
schemas, entry and functions. Nominal spelling is @module:declaration with concrete
arguments; the runtime string name is str. See [IR](ir.md): preserve provenance,
concrete types and symbolic constants; diagnostics make output non-executable.
for evaluates once using single/pair ITER_SNAPSHOT; continue advances and loop transfers
unwind only exited handlers. match stores its subject once, uses ENUM_IS/ENUM_GET and
fresh arm bindings, and traps on an unmatched runtime subject. JSON_DECODE carries the
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
| image_transport, image_worker | Bounded module and bulk transport |
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
