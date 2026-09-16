# Data semantics contract (language 0.8)

[中文](../../../zh-CN/reference/language/data.md) · [English](data.md) · [Documentation](../../README.md)

PixelLang distinguishes a binding (a name referring to a value) from an object.
The following rules retain the existing model and make its consequences explicit.
They do not introduce implicit deep immutability or implicit object copying.

| Operation | Contract |
| --- | --- |
| `let name = value` | The binding cannot be assigned again. Referenced fields/elements may be changed. |
| `var name = value` | The binding may be assigned a value of the same static type. |
| Function parameter | Immutable binding; an object argument is shared with the caller. |
| Assignment / argument passing | Scalar value copied; object reference shared. Rebinding a local does not rebind the caller's name. |
| Field / index update | All aliases observe the changed object. Bounds/type checks still apply. |
| `collections.Copy(values)` | Fresh outer array, shared elements. Copying an array of records does not copy records. |
| Independent records | Construct a new record; explicitly copy nested objects that must be independent. |
| Scalar equality | Matching int, float64, bool and string types compare by value, without numeric coercion. |
| Object equality | Arrays, maps, records, enums, JSON and functions are not equality-comparable. Compare intended fields explicitly. |
| Iteration | Shallow snapshot of the outer collection at entry; nested objects remain shared. |
| Closures | Capture bindings; sibling closures share captured mutable cells. Each declaration/iteration creates fresh bindings. |
| Recoverable failure | Earlier mutations remain visible. Catch does not roll back a transaction. |
| Replay | Restores historical object identities and mutations; observing a past state does not rerun external writes. |

Example: sharing and shallow copying

```pixellang
import "std/collections.pxl" as collections
record Item { amount: int }
fn change(item: Item) { item.amount = 9 }
fn main() {
    let original = [Item{amount: 1}]
    let copied = collections.Copy(original)
    change(copied[0])
    print(original[0].amount) // 9: the Item is shared
    copied[0] = Item{amount: 2}
    print(original[0].amount) // 9: the arrays themselves are independent
}
```

Design tradeoff: binding mutability is simple and supports existing imperative
algorithms. It cannot promise deep immutability. Automatic deep copy would hide
costs and needs explicit cycle/closure policies; it is not the meaning of ordinary
assignment or Copy. Structural equality would similarly need recursive identity
and cycle rules. Use explicit domain comparisons rather than
silently choosing an object equality meaning.

Executable evidence: tests/test_data_semantics_v8.py plus the current closure,
collection callback and capture-cell suites.
