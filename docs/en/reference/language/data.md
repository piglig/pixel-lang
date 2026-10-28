# Values and update permissions

[简体中文](../../../zh-CN/reference/language/data.md) · [English](data.md) · [Documentation](../../README.md)

Copying ordinary values does not create shared mutation. Use let for readonly bindings, var for local updates and inout for explicit caller writeback.

## Contract and interface

Ordinary records, arrays, maps, and enums have value semantics. `let` holds a
readonly value; `var` permits local updates, including fields and nested containers.
Assignment, arguments, and return values do not introduce observable shared mutation.

| Form | Meaning |
| --- | --- |
| `let values = [1]` | No reassignment or element updates. |
| `var values = [1]` | Reassignment, element updates, and `values.append(2)` are allowed. |
| `var copy = original` | Updating copy preserves original. |
| `item with { count: 2 }` | A new record of the same type; unspecified fields are preserved. |
| Ordinary parameters | Readonly; assign to a local var to edit a copy. |
| `values: inout [int]` | Explicit caller writeback, called as `sort(&values)`. |
| Closure captures | Readonly values captured at creation. |
| Historical snapshots | Later changes preserve old values; replay does not repeat external writes. |

```pixellang
record Item { values: [int] }
effect fn main() {
    let original = Item{values: [1]}
    var edited = original
    edited.values.append(2)
    let replaced = original with { values: [3] }
    print(original.values) // [1]
    print(edited.values)   // [1, 2]
    print(replaced.values) // [3]
}
```

The implementation may share storage. Value isolation does not require a deep copy
on every assignment or guarantee allocation-free updates. Array and map updates
copy affected storage paths; resource limits and cancellation still apply.

Values and runtime identity are distinct. Space snapshots are values; entities,
links, and live flow instances have explicit identities. Copying a flow handle
does not create an independent instance; create that instance explicitly.

These value semantics are implemented in the bundled development compiler and runtime.
The legacy `mut`/`Frozen` type wrappers and `freeze`/`thaw` builtin conversions have
been removed; same-name user declarations follow ordinary name resolution.

Recoverable failure uses `Result` and explicit `?` propagation; see [errors](errors.md).

## Structural equality

This contract is implemented in the runtime, self-hosted checker and bundled
development compiler, with targeted source and program PNG round-trip verification.
See [self-hosting verification](../../internals/self-hosting.md) for the current development artifact’s acceptance scope. `==` and `!=` require identical static operand types. Integers,
float64, booleans, strings, and Point3/Vec3 in the same frame are comparable. Arrays,
typed maps, records and enums are comparable when all their element or field
types are comparable, including inactive enum variants and empty container element
types. Unconstrained type parameters, functions, JSON, Flow, Space, Entity, Link and
Transform3 do not support direct equality.

Arrays compare order and elements; maps compare key sets and corresponding values
without regard to insertion order; records compare fields of the same nominal
type; enums compare the variant and payload of the same nominal type. Source
locations, allocation addresses and internal sharing topology do not affect value
equality. Ordinary values do not provide mutable reference cycles; spatial graphs
express cycles through explicit entity and link identities. Comparison does not
mutate objects or allocate VM heap values.
Runtime traversal has an independent work budget and supports cancellation.
Float64 equality is exact; geometric proximity requires an explicit tolerance.

State initializer comparisons must have the same content semantics, including
independence from field spelling order and map insertion order. Runtime tests are
in `tests/test_value_equality.py`; native compilation and PNG round-trip tests are
in `tests/test_native_equality.py`.

Value update and isolation tests: `tests/test_native_value_assignment.py`,
`tests/test_native_value_captures.py`, and `tests/test_native_collection_methods.py`.

## Unicode categories

`unicodeCategory(text)` accepts one character and returns its two-letter Unicode general category, such as `Lu`, `Ll`, or `Lo`. Classification uses the bundled Unicode **15.1.0** table in both the reference runtime and native backend; it does not depend on the host Python Unicode version. Empty or multi-character input is rejected.

## Further reading

- [Documentation index](../../README.md)
- [Language overview](overview.md)
