# Function values and lexical closures (language 0.8)

[中文](../../../zh-CN/reference/language/functions.md) · [English](functions.md) · [Documentation](../../README.md)

Function values, anonymous functions and captures are supported in text and semantic
pixel programs.
## Types and calls

Function types use `fn(T, U) -> R`; a unit result omits the arrow, as in `fn(int)`.
Parameter names are not part of type identity. Types are invariant and require exact
parameter/result matches, including explicit numeric conversions.

`[fn(int) -> string]` is an array of functions; `fn(int) -> [string]` returns an
array. Functions may accept or return other functions. Named functions, qualified
references such as `library.transform`, and explicitly specialized references such
as `library.identity[int]` can be stored or passed as callbacks. Specialization and
module access checks are the same as for direct calls. Private caller types remain
usable as arguments to public generic functions.

Calls evaluate the callable expression first, then arguments left to right, exactly
once each. Returned functions (`choose()(42)`), record fields and indexed functions
can be called directly. A failed target read prevents argument effects. Changing a
stored function during argument evaluation does not replace the selected target.
Lexical locals shadow module aliases for field selection and invocation.

## Anonymous functions and captures

Anonymous functions have named parameters and either a block or an expression
body. Parameter annotations may be omitted when a function context supplies them.
An omitted result is inferred from context or consistent return expressions; a
non-unit function must return on every path. Named function signatures remain
explicit. Examples: `fn(x) = x + 1` in an `fn(int) -> int` context, and
`collections.Map(values, fn(x) = string(x))`. Missing input evidence is an error,
not a dynamic type. Generic checking uses callback results as output constraints.

An explicitly annotated closure remains valid:

```go
fn counter() -> fn() -> int {
    var count = 0
    return fn() -> int {
        count += 1
        return count
    }
}
```

Closures capture lexical bindings. Closures capturing the same mutable binding
observe assignments by the defining function and sibling closures. Immutable
bindings cannot be rebound, but referenced objects retain shared mutable identity.
Captures remain alive after the defining call returns. Nested closures carry access
to required outer bindings transitively; separate calls create separate environments.

Every entry into a declaration creates a fresh binding. For iteration, pattern and
catch bindings are also fresh on each entry. A closure escaping one iteration keeps
that iteration's binding. Recursive closures can be formed by assigning a lambda to
an already initialized mutable function binding; a declaration's own name is not
available in its initializer.

Function values have no equality or JSON representation. Equality and JSON decoding
are rejected by checking; JSON encoding raises json.unsupported_type, including
functions encountered inside collections or records.

## Pixels, cells and replay

Semantic pixels encode function types, anonymous-function references and separate
body regions. Decoding reconstructs nested anonymous ASTs; duplicate, missing,
reused and unreferenced body identities are rejected. Capture requirements are
recomputed from lexical references, independent of authoring metadata.

`captures.py` assigns declaration identities and propagates transitive captures.
The checker resolves capture types, mutability and slots. Each concrete anonymous
body has a checked VM function with explicit parameter and captured-cell signatures.

CAPTURE boxes a binding once and reuses its cell. BIND enters a fresh binding; STORE
updates the existing cell, and LOAD reads its value. CLOSURE constructs a callable
with traced captured cells. INVOKE passes those same cells to the anonymous body.
GC follows both callable environments and cell contents, including cycles. Machine
snapshots preserve identities; debugger value traversal unwraps cells.

The internal type spelling uses a delimited result (`fn(int)->(str)[]`). Pixel types
likewise delimit the result to distinguish outer arrays from result arrays. Unit is
explicit in pixels and omitted in authoring display.

## Related tests

- `tests/test_function_types_v6.py`: text and pixel type roundtrips.
- `tests/test_function_values_v6.py`: named/specialized callbacks, call order,
  module shadowing, source recovery and negative type/access checks.
- `tests/test_captures_v6.py`: lexical identities and transitive capture analysis.
- `tests/test_capture_cells_v6.py`: cell sharing, fresh bindings, GC and checkpoints.
- `tests/test_closures_v6.py`: escaped counters, independent environments, siblings,
  iteration bindings, nested generic captures, GC, recursion, immutable bindings,
  PNG-only execution, every-step replay, navigation and debug locals.



Generic callback algorithms are now available in `std/collections.pxl`; see
[collections](collections.md) and the runnable callback-analysis example.

Closure environment views now hide internal function identifiers and label captures
with source names. Paging and reverse-time views retain reference paths. Nested
anonymous-function formatting is indented and idempotent. Additional tests reject
malformed capture schemas, direct calls that bypass environments, captured entries,
and missing or unreferenced lambda body regions.
