# PixelLang v0.1 — Architecture Decision Records

**Status:** Accepted / Frozen for v0.1  
**Date:** 2026-08-19  
**Authority:** PixelLang Design Decision Freeze  
**Scope:** Architecture decisions only. This document does not define a complete
language grammar, pixel encoding table, file-format layout, IR, or VM instruction
set.

This decision compendium is normative for every later PixelLang v0.1
specification and implementation. A later specification may make a decision more
precise, but it MUST NOT contradict or silently redefine an Accepted ADR.

The product context is defined by [PRD v0.1](../prd.md).

## Reading this document

- **MUST**, **MUST NOT**, and **MAY** are normative.
- A *Semantic Pixel Descriptor* is a logical decoded unit. It is not a raw RGBA
  value and is not necessarily an AST node.
- *Carrier* means the visible source-space structure that represents a semantic
  object. It is not metadata.
- An *Atom Packet* is a fixed source-space representation for an atomic literal
  or identifier.

## Decision index

| ADR | Decision |
| --- | --- |
| ADR-001 | Spatial language identity |
| ADR-002 | Physical source representation |
| ADR-003 | Semantic/physical separation |
| ADR-004 | Pixel, Semantic Pixel, Atom, and Region |
| ADR-005 | Sources and precedence of spatial semantics |
| ADR-006 | Geometry and presentation layout |
| ADR-007 | Explicit relationships and Connection carriers |
| ADR-008 | Deterministic Region model |
| ADR-009 | X/Y defaults and execution order |
| ADR-010 | Semantic Pixel Descriptor and core kinds |
| ADR-011 | Atom Packet payload encoding |
| ADR-012 | Binding, assignment, and scope |
| ADR-013 | Expressions and statement ordering |
| ADR-014 | Conditional and minimal type system |
| ADR-015 | Errors and runtime numeric behavior |
| ADR-016 | Canonical representations and equivalence |
| ADR-017 | Canonical rendering and planar embedding |
| ADR-018 | Source formats, annotations, and provenance |
| ADR-019 | Source/runtime separation and dimensionality |
| ADR-020 | Versioning and v0.1 scope |

---

## ADR-001 — Spatial language identity

**Status:** Accepted

### Decision

PixelLang is a Spatial Programming Language. Its language core is the Spatial
Semantic Model, not a text program rendered as an image. The compiler MUST NOT
use OCR as a parsing stage.

### Consequences

The compiler constructs spatial semantic structures directly from decoded pixel
data. A text-token stream is not the primary source representation or required
intermediate form.

---

## ADR-002 — Physical source representation

**Status:** Accepted

### Decision

The v0.1 physical source representation is a two-dimensional RGBA Pixel Grid.
`.pixel` is the native source format; PNG is a visualization and exchange format,
not the required v0.1 compiler input.

### Consequences

The standard v0.1 compile entry point is `pixelc program.pixel`. PNG conversion
is a separate import/export operation. A later version MAY add direct PNG input
without redefining the language.

---

## ADR-003 — Semantic/physical separation

**Status:** Accepted

### Decision

Language semantics MUST be separated from RGBA encoding:

```text
RGBA → Physical Decoder → Semantic Pixel Descriptor → Spatial Semantic Model
```

The Language Specification MUST NOT assign meaning directly to RGB or RGBA
numbers. Physical mappings are versioned encoding concerns.

### Consequences

Changing a palette, color space, container format, or visualization format does
not itself change PixelLang language semantics.

---

## ADR-004 — Pixel, Semantic Pixel, Atom, and Region

**Status:** Accepted

### Decision

A Pixel is the smallest physical unit and a local semantic carrier. A Semantic
Pixel is the decoded logical form of a Pixel. Neither is automatically a token or
AST node.

After spatial interpretation, Semantic Pixels MAY form an AST Atom or MAY be
members of a Region. Region is the higher-level compositional spatial structure.

### Consequences

The compiler MUST NOT implement a one-Pixel-to-one-token or one-Pixel-to-one-AST-
node model.

---

## ADR-005 — Sources and precedence of spatial semantics

**Status:** Accepted

### Decision

Spatial semantics are jointly determined by:

```text
Position
Geometry-derived Relationship
Explicit Spatial Relationship
Region
Context
```

The precedence order is:

```text
Explicit Spatial Relationship
    > Implicit Geometry
    > Presentation Layout
```

### Consequences

An explicit valid relationship overrides a conflicting default geometric inference.
Presentation-only layout MUST NOT override either semantic relationship.

---

## ADR-006 — Geometry and presentation layout

**Status:** Accepted

### Decision

v0.1 implicit adjacency uses 4-neighbor connectivity only: left, right, up, and
down. Diagonal adjacency and adjacency across empty space do not exist by default.

X provides default ordering/direction; Y provides default hierarchy/grouping.
Neither axis directly means execution time or lexical scope.

Presentation geometry is limited to rendering scale, canvas margin, and whole-
program translation. Those transformations preserve semantics. Altering actual
Source Grid adjacency, direction, boundary, containment, Region membership, or
connection topology MAY alter semantics.

### Consequences

Inserting transparent Pixels between source Pixels is not a guaranteed
semantics-preserving formatting operation. This decision supersedes earlier
provisional wording that treated source-grid pixel spacing as presentation-only.

---

## ADR-007 — Explicit relationships and Connection carriers

**Status:** Accepted

### Decision

v0.1 supports the following explicit relationship kinds:

```text
Connection
Boundary
Containment
Ordering
Grouping
```

Connection is a first-class semantic object represented in source by a visible,
connected Connection Carrier. Its semantic content is:

```text
source endpoint + target endpoint + relation kind + directedness
```

Carrier length, bend count, and Manhattan route are presentation details.

Connection Carrier rules in v0.1:

- exactly one source and one target endpoint;
- no branching, crossing, shared carrier Pixels, or shared carrier paths;
- endpoints attach only through explicit Ports;
- it MAY cross the empty geometric extent of a Region or attach at a Boundary
  Port, but MUST NOT occupy, overlap, or share ordinary Region-content Pixels;
- it does not alter endpoint Region membership automatically.

Relationship carriers are assigned as follows:

| Relationship | Carrier / source form |
| --- | --- |
| Connection | Connection Carrier |
| Boundary | closed Structural Region boundary |
| Containment | strict geometric enclosure by a Structural Region boundary |
| Ordering | directed Connection with `Ordering` kind |
| Grouping | Structural Region with `Group` kind |

### Consequences

There are no free-floating Boundary or Containment objects in v0.1. Grouping does
not itself create lexical scope.

---

## ADR-008 — Deterministic Region model

**Status:** Accepted

### Decision

Region recognition MUST be deterministic. A Pixel belongs to exactly one Region
nesting chain. Regions MAY nest; arbitrary overlap is forbidden. If more than one
legal parse remains, compilation MUST fail rather than guess.

Only the following source-space structures produce Regions:

```text
Atom Packet
Structural Region
Connection Carrier
```

Arbitrary connected components MUST NOT be promoted to semantic Regions merely
because they share a color class.

A Structural Region consists of a Structural Header, a closed boundary, and
content. v0.1 Structural Region boundaries MUST be axis-aligned, rectangular, and
closed.

### Consequences

Free-form Region shapes, automatic semantic clustering, and arbitrary overlap are
outside v0.1. Same source and same accepted versioned rules MUST yield the same
Spatial AST or the same diagnostic.

---

## ADR-009 — X/Y defaults and execution order

**Status:** Accepted

### Decision

X and Y supply spatial defaults only:

```text
X → ordering / direction
Y → hierarchy / grouping
```

Spatial order and execution order are separate. The Canonical AST determines
evaluation and execution order.

### Consequences

Coordinates cannot independently impose runtime timing or lexical scope. Statement
sequence is determined by ADR-013.

---

## ADR-010 — Semantic Pixel Descriptor and core kinds

**Status:** Accepted

### Decision

The logical decoded form is:

```text
Semantic Pixel Descriptor
├── ColorClass
├── Role
└── Payload Fragment
```

The minimum v0.1 ColorClass set is:

```text
Background
Literal
Identifier
Operator
Structural
Connection
Boundary
Annotation
```

The supported Operator kinds are:

```text
ADD SUB MUL DIV
LT LE GT GE EQ NE
```

The supported Structural kinds are:

```text
Program
Block
Expression
Bind
Assign
Conditional
Predicate
Then
Else
Group
```

Operator and Structural kind identity MUST be carried by a versioned logical Kind
Code. It MUST NOT be inferred from a raw RGBA value.

### Consequences

ColorClass expresses semantic family, not complete language meaning. The Pixel
Encoding Specification defines the physical decoding table for descriptors and
Kind Codes without changing this ADR.

---

## ADR-011 — Atom Packet payload encoding

**Status:** Accepted

### Decision

Integer literals and identifiers use a fixed `9 × 8` Atom Packet:

```text
[Marker][Bit 0] ... [Bit 7]
[Marker][Bit 8] ... [Bit15]
...
[Marker][Bit56] ... [Bit63]
```

The left column is the Marker/Anchor column. The right `8 × 8` cells form a
payload grid read row-major from MSB to LSB. Every payload slot MUST be an opaque
Semantic Pixel; both `Bit(0)` and `Bit(1)` are valid Payload Fragments. Transparent
Pixels MUST NOT represent zero bits.

Integer payloads encode signed two's-complement `int64` values. Identifier payloads
encode fixed 64-bit NameKeys. The complete Packet is one connected Atom Region.

### Consequences

Payload has no ambiguity with Empty Space and cannot be split by transparent holes.
Human-readable aliases MAY be represented only as Annotation and have no binding
semantics.

---

## ADR-012 — Binding, assignment, and scope

**Status:** Accepted

### Decision

v0.1 distinguishes:

```text
Bind(name, initializer)
Assign(target, value)
```

Bind introduces a binding in the current lexical scope and MUST include an
initializer. Assign targets an existing resolvable binding. Repeated Bind in the
same scope is a compile error; a child scope MAY shadow a parent binding.

Only these Region kinds create lexical scope:

```text
Program
Block
Then
Else
```

NameKey is source-level identity. Canonical AST comparison resolves source
NameKeys to canonical Binder IDs using lexical binding resolution. Consistent
renaming of a binding and all its references is semantic-preserving
alpha-equivalence; original NameKeys remain source provenance.

### Consequences

Scope is a language structure, never a direct consequence of Y coordinate.
Undefined identifiers and invalid assignment targets are compile errors.

---

## ADR-013 — Expressions and statement ordering

**Status:** Accepted

### Decision

Spatial structure constructs expressions; the Canonical AST defines evaluation.
Nested expressions MUST be represented by nested Expression Regions or Explicit
Connections. v0.1 MUST NOT use traditional textual precedence as an implicit
fallback for an insufficiently structured expression.

Within a Block, root statements receive default order only when their X intervals
are strictly non-overlapping and produce one ascending-X order. Explicit directed
`Ordering` Connections override or supply ordering when necessary. Equal,
overlapping, cyclic, or otherwise non-unique statement order is a compile error.

### Consequences

The compiler cannot silently guess a parse for a visually linear but structurally
ambiguous expression or statement sequence.

---

## ADR-014 — Conditional and minimal type system

**Status:** Accepted

### Decision

v0.1 Conditional is a statement, not an expression:

```text
ConditionalStatement
├── Predicate : Boolean
├── ThenBlock
└── ElseBlock
```

The minimal static type rules are:

```text
Arithmetic:          Integer × Integer → Integer
Ordering comparison: Integer × Integer → Boolean
Equality:            T × T → Boolean, T ∈ {Integer, Boolean}
Predicate:           Boolean required
Bind:                infer type from initializer
Assign:              exact type match
Implicit conversion: forbidden
```

### Consequences

Conditional value merging, expression-valued branches, floating-point types, and
implicit coercions are outside v0.1.

---

## ADR-015 — Errors and runtime numeric behavior

**Status:** Accepted

### Decision

PixelLang uses fail-fast, precise diagnostics.

Compile-time errors include unknown or invalid semantic descriptors, malformed
payloads, ambiguous Regions, invalid Connections or containment, unresolved
identifiers, invalid bindings, ordering ambiguity, and type errors. Such errors
MUST prevent executable output.

Runtime errors include integer division by zero and integer overflow. The VM MUST
report runtime errors with source provenance. Integer division truncates toward
zero; `INT64_MIN / -1` is integer overflow.

### Consequences

The compiler does not reject a program merely because a runtime error is possible.
All diagnostics retain source position, Region, and spatial context where
available.

---

## ADR-016 — Canonical representations and equivalence

**Status:** Accepted

### Decision

PixelLang has two canonical representations:

```text
Canonical Spatial AST
Canonical Pixel Source
```

Canonical Spatial AST is the sole semantic truth. Canonical Pixel Source is its
standard spatial encoding. Inconsistency between the two is an error; neither is
chosen silently.

Two source programs are semantically equivalent exactly when their Canonical
Spatial ASTs, including canonical binding identities, are equal.

### Consequences

Whole-program translation, renderer scale, canvas margin, and consistent binding
renaming preserve semantics. Changes to semantic direction, connections,
containment, Region membership, branch structure, or binding graph do not.

---

## ADR-017 — Canonical rendering and planar embedding

**Status:** Accepted

### Decision

Every valid v0.1 Spatial AST MUST have a non-crossing 2D planar embedding.
Canonical Pixel Source MUST be rendered deterministically from Canonical Spatial
AST:

```text
Source
  → Spatial AST
  → Canonical Spatial AST
  → Canonical Pixel Source
```

The renderer MUST normalize the origin, use canonical Structural Region frames,
place children in canonical structural order, and use fixed non-crossing Manhattan
routing. It MUST satisfy:

```text
parse(CanonicalPixelSource(AST)) == AST
```

An AST without a valid non-crossing v0.1 embedding yields
`NonCanonicalizableSpatialGraph`.

### Consequences

Crossing-based notation and non-planar spatial graphs are outside v0.1. Original
layout may be retained as provenance but is not canonical semantic truth.

---

## ADR-018 — Source formats, annotations, and provenance

**Status:** Accepted

### Decision

Fully transparent Pixels are the sole v0.1 Background / Empty Space. Opaque
Background Pixels are invalid. Fully opaque Pixels are semantic candidates;
semi-transparent Pixels are unsupported. Unknown, undefined, or invalid colors
are compile errors.

Annotation is non-semantic:

- it participates in no adjacency, Region, Connection, Context, or AST rule;
- it MUST NOT occupy an Atom Packet, payload, Boundary, or Connection Carrier;
- it MAY occupy only semantically unused source positions;
- Canonical Pixel Source omits it.

Compiler representations MUST preserve original source coordinates, decoded
physical color information, Region bounds, and relevant spatial context as
provenance for diagnostics, debugging, and source mapping.

### Consequences

Annotation cannot alter program meaning or become a hidden relationship layer.
Canonical sources are free of non-semantic editor material.

---

## ADR-019 — Source/runtime separation and dimensionality

**Status:** Accepted

### Decision

Source Program is separate from Runtime State and Runtime Trace. Runtime may
reference source provenance for visualization and diagnostics, but MUST NOT write
back into Source Program by default.

v0.1 implements only 2D source. Its Position, Spatial Relation, and Region data
models MUST NOT irreversibly assume exactly two dimensions. Future 3D extends:

```text
Position(x, y) → Position(x, y, z)
Pixel → Voxel
```

Z is spatial, not temporal. Time is an independent future Temporal Dimension `T`.

### Consequences

v0.1 has no Z or T execution semantics. Future 3D and temporal work must preserve
the separation of spatial and runtime concerns.

---

## ADR-020 — Versioning and v0.1 scope

**Status:** Accepted

### Decision

PixelLang versions Language, Encoding, File Format, Compiler, and VM separately.
Language semantics MUST NOT change implicitly with compiler version. Breaking
language changes require a Language Version change.

The v0.1 MVP includes only:

```text
Integer
Boolean
Arithmetic
Comparison
Variable
Assignment
Expression
Basic Conditional
```

Function, loop, module, import/export, string, byte, aggregate data, complete VM
runtime services, 3D source, temporal semantics, bootstrap, and self-hosting are
not v0.1 MVP requirements.

### Consequences

All later specifications and implementation plans MUST keep this scope boundary.
Future additions require new ADRs when they alter accepted architecture decisions.

---

## Superseded provisional interpretations

The following earlier provisional interpretations are explicitly superseded:

- Physical source-grid pixel spacing is not automatically presentation-only.
- Connection route shape is not semantic in v0.1; only its declared relation,
  endpoints, directedness, and topology are semantic.
- Runtime errors do not cause compile-time rejection merely because they are
  possible.
- NameKey spelling is not part of canonical semantic identity after binding
  resolution.
