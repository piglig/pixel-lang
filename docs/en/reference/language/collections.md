# Generic collection callbacks (language 0.8)

[中文](../../../zh-CN/reference/language/collections.md) · [English](collections.md) · [Documentation](../../README.md)

The runtime provides two general array bulk operations:

| Builtin | Behavior |
| --- | --- |
| `extend(destination, source)` | Append the source elements in order; mutate and return destination. Both arrays have the same element type. Self-extension duplicates the original contents once. |
| `arrayRepeat(values, count)` | Return a fresh outer array containing `count` copies of the input sequence. Count must be a nonnegative `int`; zero returns an empty array. |

Both operations are shallow: element references remain shared. They enforce the
VM heap item budget before allocating or extending and retain argument references
across garbage collection. They are ordinary typed collection operations, available
to both text and semantic-pixel programs; neither interprets image or compiler data.
Bulk work remains proportional to the number of elements even though it uses a
single VM instruction, so benchmark reports must include wall time as well as steps.

Import `std/collections.pxl`. These functions are written in PixelLang, bundled as
semantic pixels, and use ordinary checked generic function values and closures.
Type arguments may be inferred from argument values and callback signatures.
In the language, callbacks may use contextual parameters and expression bodies,
for example `Map(values, fn(value) = string(value))`. Explicit types remain available.

| Function | Callback | Result |
| --- | --- | --- |
| `Map[T, U](../../../reference/language/values, transform)` | `fn(T) -> U` | fresh `[U]` |
| `Filter[T](../../../reference/language/values, predicate)` | `fn(T) -> bool` | fresh `[T]` |
| `Fold[T, A](../../../reference/language/values, initial, combine)` | `fn(A, T) -> A` | accumulated `A` |
| `GroupBy[T](../../../reference/language/values, keyOf)` | `fn(T) -> string` | `map[[T]]` |
| `Sort[T](../../../reference/language/values, less)` | `fn(T, T) -> bool` | fresh sorted `[T]` |

Map, Filter, Fold and GroupBy take the language's shallow iteration snapshot at
entry. They visit values once in input order. Callback changes to the input's outer
array do not change the visitation sequence; referenced objects remain shared.
GroupBy calls keyOf once per item, preserves item order within each group, and
inserts groups in first-occurrence order. Empty input never invokes a callback;
Fold returns its initial value unchanged.

Sort uses stable bottom-up merge sort with O(n log n) comparisons and O(n) auxiliary
storage. It copies the input's outer array before invoking the comparator and never
reorders that input. Equal-order items retain their input order. Element objects
remain shared; this is not a deep copy. The comparator must define a consistent
strict ordering. Its call order is deterministic for an execution, but programs
should not depend on an exact comparison sequence across algorithm revisions.

Callbacks can capture bindings and produce recoverable errors. An error immediately
stops the algorithm and propagates to the caller's handler. Earlier callback effects
are not rolled back. In particular, Sort's copied storage does not undo deliberate
callback changes to the original array or to shared objects.

`tests/test_collection_callbacks_v6.py` compares sorting with Python's independent
oracle across empty, duplicate, sorted, reverse and seeded random inputs. It also
checks stable records, aliasing, empty callbacks, side effects, failure propagation,
PNG/text recovery and every intermediate replay state for a callback pipeline.

`examples/callback-analysis` is a runnable two-source project. It combines optional
JSON configuration, captured filtering, stable record sorting, mapping, float
aggregation and grouping. Run `pixel test examples/callback-analysis` from the repo
(or the equivalent `python -m pixellang test ...` in the development environment).
