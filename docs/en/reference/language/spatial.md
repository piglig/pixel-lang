# Spatial semantics 0.8

[中文](../../../zh-CN/reference/language/spatial.md) · [English](spatial.md) · [Documentation](../../README.md)

Coordinates are nonnegative integer tuples `(X,Y)` for this version. Semantic structures use arbitrary-dimensional source points internally; physical loading currently requires two dimensions. The source model does not assign time to a spatial coordinate. Studio separately displays execution time along its third visual axis.

## Deterministic region detection

The following row/indentation rules apply to sources without explicit `region`
markers. Marked sources use the compact-region rules below.

1. Decode all nontransparent pixels to semantic tokens; invalid colors are errors even in unreachable code.
2. Group occupied tokens by exact Y coordinate. A row is a candidate statement region, not an arbitrary flood-filled component.
3. With no explicit next links, sort tokens by increasing X. Consecutive tokens may have horizontal distance 1, 2 or 3 (zero, one or two empty cells). Larger gaps are disconnected-region errors. Diagonal or vertical neighbors never join an expression.
4. If a row has any next link, the links must form one directed, acyclic, unbranched chain containing every semantic token in that row. Following that chain supplies order instead of X. Arbitrary horizontal distances and reversed direction are allowed. Cross-row next links, branches, cycles, uncovered tokens, duplicate edges and dangling endpoints are errors.
5. Region indentation is its minimum physical X, independent of token traversal direction. Process regions in increasing Y; pop the indentation stack while its last region has indentation greater than or equal to the current region. The remaining top region is the default parent; an empty stack gives module scope. Leading horizontal translation is legal.
6. An explicit child link overrides the target region's default parent. Both endpoints may be any token in their respective rows. Parent Y must be strictly less than child Y; each child can have at most one explicit parent. Other rows keep their own independently computed default parents.
7. Only if, else, while, for, try, catch and function regions can contain child statements. Grammar then checks semantic compatibility: operand placement, headers, type tokens and scope. Empty blocks are valid. Siblings retain increasing Y order.

## Relationship interpretation

| Relationship | Meaning |
|---|---|
| Adjacency | row tokens at unit horizontal distance |
| Direction | increasing X default, explicit next traversal override |
| Distance | gaps of at most two empty cells by default |
| Connectivity | implicit row run or full explicit next chain |
| Containment | indentation-derived or explicit parent region |
| Boundary | row boundary separates statements; dedentation separates scopes |
| Connection | validated `next` and `child` edges |
| Ordering | token order plus deterministic increasing-Y statement order |

Region bounds are geometric bounding boxes for diagnostics, not independent semantic scope boundaries. Exact pixel sets and the parent graph are retained to avoid claiming that every pixel inside a rectangle belongs to the region. Transparent rows are layout only. There are no dedicated boundary-colored tokens in the current encoding.

Changing color, horizontal operand positions or indentation may change semantics. Translation of an entire program and spacing changes inside the distance limit preserve semantics. Color display legends and UI cell size do not affect source bytes.

## Explicit compact regions

A `region` connection points from a semantic token to itself and marks that token
as a region head. A source containing any such marker uses explicit region mode:

1. Markers are unique. Region order is head order by `(Y, X)`, permitting several
   statements on the same physical row.
2. On a row without `next` links, consecutive tokens follow increasing X, with
   distance at most three; a new marked head starts a new region.
3. If a row contains any `next` link, every region on that row uses explicit chains.
   Heads cannot have incoming links; chains cannot branch, merge, cycle or cross
   rows. All semantic tokens must belong to exactly one marked region.
4. Regions default to module scope. Only `child` links establish containment; the
   parent must precede the child in region order, including when both share Y.
   Each child has at most one parent, and only grammar block openers can contain
   child statements. Indentation does not infer scope in this mode.

The PixelLang emitter packs complete statement regions into shelves, retaining
token order and explicit parent relationships. Consecutive tokens need no redundant
`next` edges. Markers and parent edges are structural source connections; no AST,
text program or bytecode is stored in image annotations. All existing pixel and
dimension limits remain enforced. Unmarked sources retain the row rules above.
