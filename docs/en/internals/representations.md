# Host reference AST and canonical representation

[中文](../../zh-CN/internals/representations.md) · [English](representations.md) · [Documentation](../README.md)

Scope: Python bootstrap/reference and spatial inspection utilities. The self-hosted
compiler uses module-local typed node arenas described in
[compiler interfaces](compiler-interfaces.md). The representations below should
not be mistaken for its internal data structures.

Spatial AST inspection includes `dimensions`, `regions`, `connections`, and a `semantic_structure` tree. Each region has an ID, exact source points, bounding box, parent ID, explicit-parent flag and ordered decoded tokens. This preserves position, connectivity and semantic grouping.

Canonical AST uses `Node(kind,data,span)`. Kinds include declarations, control flow, expression statements, array/map/record constructors and access/mutation, builtins and typed json_decode. let nodes carry mutable; fn nodes carry entry; for nodes optionally carry a second binding. Else regions become the preceding if's `otherwise` list. Parameters retain names/types. Semantic analysis records `_type`, `_slot`, `_function`, `_locals`, and `_region`; `_else_span` preserves the alternative region. Provenance is retained through type checking, lowering and assembly.

`Span` contains source identity and exact coordinate tuples. Serialized form additionally gives component-wise `min`/`max` for highlighting. Composite expression spans merge operands and operators; statement spans include the whole row. Parentheses/call punctuation need not be included in an expression's minimal runtime span, but remain available in its source region.

## Canonicalization

`normalize(source)` decodes and parses, then encodes the semantic AST as a regular grid:

- Region rows use Y=0,2,4,… in semantic execution order.
- First token X=2×nesting depth; subsequent tokens are spaced by two.
- Unary/binary expressions are fully parenthesized.
- Bare expression statements become explicit do statements that discard their result.
- Explicit relation graphs are expressed through default canonical layout.
- Nonsemantic layout metadata and redundant transparent cells are discarded; approved name/source annotations may be retained.
- Native JSON serialization uses sorted keys for stable bytes.

`canonical.fingerprint(tree)` gives deterministic JSON of semantic fields, omitting spatial spans and internal analysis annotations. It normalizes layout identity, not arbitrary equivalence (e.g. constant folding, renaming or commutative arithmetic). Two syntactically different but mathematically equivalent programs need not have identical canonical sources.

Required property: parsing and executing a normalized source preserves program meaning and module references. Normalization is idempotent. Formatting imports does not require resolving dependencies; semantic compilation still validates them. Original and canonical source bytes need not match.

The AST includes enum/variant declarations, enum_value, expression and statement match with
case bindings, function_value, invoke and lambda nodes. The type tree represents
generic parameters/arguments and function signatures compositionally. Captures are
recomputed from lexical declarations; internal slot/capture annotations do not
replace executable semantic pixels.


The text-authoring AST may temporarily omit anonymous parameter/result types
while collecting contextual constraints. Expression anonymous bodies become return
nodes with source spans. Checking resolves every executable signature before
semantic pixels are encoded; decoded pixels never require missing authoring
annotations or source text to infer their meaning.
