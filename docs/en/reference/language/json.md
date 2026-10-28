# Typed JSON

[简体中文](../../../zh-CN/reference/language/json.md) · [English](json.md) · [Documentation](../../README.md)

Use jsonDecode to convert external data into validated language values. Shape mismatches return Result; resource limits remain execution boundaries.

## Contract and interface

`jsonDecode::[T](input)` returns `Result[T, Error]` with a statically checked success value. Input may be a string or an
already parsed immutable `json` value. This remains a checked intrinsic using the same type-argument syntax as user-defined
generic functions. Existing `std/json.pxl` operations remain
useful for inspecting dynamically shaped documents.

```pixellang
record Item { title: string, counts: [int] }
effect fn main() {
    match jsonDecode::[Item](input()) {
        .Ok(item) => { print(item.title) },
        .Err(error) => {
            print(error.code)
            print(error.path)
        }
    }
}
```

## Target types

Supported targets: int, finite float64, bool, string, json, arrays, typed maps,
Option, tagged enums and declared records (including qualified imported records and recursive schemas with finite
input). Record visibility is checked. Required record fields must be present; unknown fields fail. Optional fields decode
missing values and null to None, with explicit handling in the program. JSON null
is also valid for json. There are no implicit string-to-number conversions. Tagged
enum envelopes and nested Option rules are documented in [enums](enums.md).
An int must be mathematically integral and fit signed 64 bits; exponent notation
is accepted when exact. Structured Error values describe recoverable conversion failures. Function values cannot be
decoded or encoded as JSON.

## Error handling

A failed conversion returns `.Err(error)`; successful conversion returns `.Ok(value)`.
Handle the result with `match`, or propagate it with `?` from a compatible Result-returning
function. Error paths identify locations such as `$[0].amount`; malformed JSON retains
the parser diagnostic. Resource limits and cancellation terminate execution.

## Values and resource limits

Decoded records and containers follow ordinary value semantics. A `let` binding is
readonly; copy a successful result into a local `var` to update fields or elements.
Local updates do not mutate other values or recorded history. There are no `mut`,
`freeze` or `thaw` type/permission forms. Partial allocations stay rooted during GC
and never escape a failed conversion. Existing JSON nesting/node and VM heap limits apply.

JSON parsing and serialization allow at most 1,000,000 characters and 100,000
value nodes, with the root at depth zero and a maximum depth of 128. Each scalar,
array and object counts as one node. Object member names label values and do not
add nodes or nesting depth; they must still contain valid Unicode scalar values.
For example, `{"a":1,"b":2}` contains three nodes: the object and its two numbers.
Parsing and serialization use the same counting rule, so member names cannot
make otherwise valid serialized data exceed the reader's structural limit.

## Delivery and recovery

The target type is carried by executable pixels and the RESULT_DECODE instruction,
not metadata. Recovery reconstructs the typed expression. Timeline replay repeats
this deterministic conversion without file I/O. Tests cover independent payloads,
exact numbers, missing/extra fields, wrong types, GC, images and recording restore.

## Further reading

- [Documentation index](../../README.md)
- [Language overview](overview.md)
