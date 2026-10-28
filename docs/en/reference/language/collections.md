# Collections and algorithms

[简体中文](../../../zh-CN/reference/language/collections.md) · [English](collections.md) · [Documentation](../../README.md)

Arrays and maps share value semantics. Basic operations use receiver methods; general algorithms live in std/collections.pxl without synonym entry points.


**On this page**

- [Contract and interface](#contract-and-interface)
- [Standard library callbacks](#standard-library-callbacks)
- [Typed maps](#typed-maps)
- [Further reading](#further-reading)

## Contract and interface

Arrays and maps follow [value semantics](data.md). Updating a container held in
`var` preserves previously stored values, including nested containers.

Basic container operations use lowercase receiver methods. General algorithms use PascalCase
functions in `std/collections.pxl`, matching other standard-library modules. Declarations, not
capitalization, determine types and exports. Each operation has one public entry point.

| Operation | Behavior |
| --- | --- |
| `values.append(value)` | Append one element to a writable array. |
| `values.extend(source)` | Append source in order; self-extension duplicates the original contents once. |
| `values.pop()` | Remove the last element and return `Option[T]`; None for an empty array. |
| `table.has(key)` | Return whether the key exists. |
| `table.keys()` | Return `[K]` in insertion order. |
| `table.get(key)` | Return `Option[V]`; None when absent. |
| `table.remove(key)` | Remove and return `Option[V]`; None when absent. |
| `table[key] = value` | Insert or replace an entry in a writable map. |

Updating a receiver requires a writable local binding or an `inout` parameter.
Ordinary parameters and captures are readonly. A map held in `let` permits `get`,
but not `remove`. Overlapping writes to the same root variable are rejected.

```pixellang
effect fn main() {
    let original = [1]
    var edited = original
    edited.extend([2, 3])
    print(original) // [1]
    print(edited)   // [1, 2, 3]
    print(edited.pop()) // Some(3)
}
```

Use `table.get(key)` for optional lookup; there is no global lookup or standard-library Get alias.
Ordinary assignment already preserves value isolation, so no Copy function is needed.

## Standard library callbacks

Import `std/collections.pxl`. Callbacks use ordinary `fn`: no external effects,
readonly parameters, and value-isolated results. Type arguments can be inferred
from arguments and callback signatures, for example
`collections.Map(values, fn(value) = string(value))`.

| Function | Callback | Result |
| --- | --- | --- |
| `Map[T, U](values, transform)` | `fn(T) -> U` | `[U]` |
| `Filter[T](values, predicate)` | `fn(T) -> bool` | `[T]` |
| `Fold[T, A](values, initial, combine)` | `fn(A, T) -> A` | `A` |
| `GroupBy[T, K: Hashable](values, keyOf)` | `fn(T) -> K` | `map[K, [T]]` |
| `SortedBy[T](values, less)` | `fn(T, T) -> bool` | sorted `[T]` |

Callbacks can update local `var` values, but not parameters or captures, and cannot
print or perform file I/O. Closures capture values at creation. Use Fold or an
explicit loop with `inout` for evolving accumulators. See [functions and effects](functions.md).

Map, Filter, Fold, and GroupBy visit values once in input order. GroupBy calls
keyOf once per item, preserves group item order, and inserts groups in order of
first occurrence. Empty input invokes no callback; Fold returns initial unchanged.
Results retain ordinary T/U without a Frozen type. Assign a result to var to edit
it while preserving the input and other results.

SortedBy uses stable bottom-up merge sort with O(n log n) comparisons and never
reorders the input. Equal-order items retain their order. The comparator must
define a consistent strict ordering. Call order is deterministic for an execution,
but its exact sequence is not guaranteed across algorithm revisions. Comparison
counts exclude callback and container storage costs.

Other pure functions:

| Function | Behavior |
| --- | --- |
| `Repeat[T](value, count)` | Return `Result[[T], Error]`; nonnegative counts produce copies, while negative counts return `collection.negative_count`. |
| `Contains[T: Equatable](values, target)` | Search by value; at most O(n) equality comparisons. |
| `Unique[T: Equatable](values)` | Preserve first occurrence order; O(n²) worst-case equality comparisons. |
| `Sorted[T: Ordered](values)` | Stable natural ordering of numbers or strings, returning `[T]`. |
| `Sum[T: Numeric](values, initial)` | Accumulate in input order, returning T; return initial for empty input. |

Collection calls do not establish new flow state transactions. Option represents
recoverable absence and Result represents recoverable failure; execution faults
such as out-of-bounds indexing terminate execution. See [errors](errors.md).

`Sorted(values)` uses natural ordering; `SortedBy(values, less)` accepts a comparator. Both return a new array and preserve the input; there is no separate integer sorting module.

## Typed maps

Declare both types: `map[K, V]`, for example `map[int, string]{7: "paid"}`.
K must satisfy [Hashable](generics.md); V must be a value type. Type parameters are
invariant, without implicit numeric or string conversions. Empty maps require an
annotation or context that determines both types.

Use `table.get(key)` when a key may be absent. Currently `table[key]` still raises
`collection.missing_key` for missing keys. `table.has(key)` tests membership and
`table.keys()` returns `[K]`. Single-variable iteration yields keys; two-variable
iteration yields keys and values from the value present at loop entry.
Insertion order is stable: replacing keeps position; deleting and reinserting
moves a key to the end. Constructors reject equal duplicate keys, including
positive and negative floating zero.

JSON uses objects for string-key maps and ordered arrays of
`{"key": ..., "value": ...}` entries for other maps. Typed decoding validates both
types and rejects duplicates. Spatial payloads, flow state, and replay preserve
values without persisting heap addresses or hash values.

Targeted tests: `test_native_collection_methods.py`, `test_stdlib_contracts.py`,
`test_native_generic_values.py`, and `test_collection_callbacks.py`.
`examples/callback-analysis` demonstrates filtering, stable record sorting,
mapping, aggregation, and grouping.

`GroupBy` infers its key type from the callback result and supports any `Hashable` type.
For example, `collections.GroupBy([1, 2, 3], fn(value: int) = value % 2)` returns `map[int, [int]]`.

`collections.Increment(&counts, key)` updates a `map[K, int]` histogram: a missing key starts
at 1, existing counts increase by 1, and integer overflow terminates execution. `K` must
satisfy `Hashable`; other copies remain unchanged.

## Further reading

- [Documentation index](../../README.md)
- [Language overview](overview.md)
