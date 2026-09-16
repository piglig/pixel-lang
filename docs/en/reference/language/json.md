# Typed JSON decoding

[中文](../../../zh-CN/reference/language/json.md) · [English](json.md) · [Documentation](../../README.md)

`jsonDecode[T](../../../reference/language/input)` returns a statically checked `T`. Input may be a string or an
already parsed immutable `json` value. This remains a checked intrinsic using the same type-argument syntax as user-defined
generic functions. Existing `std/json.pxl` operations remain
useful for inspecting dynamically shaped documents.

```pixel
record Item { title: string, counts: [int] }
fn main() {
    try {
        let item = jsonDecode[Item](../../../reference/language/input())
        print(item.title)
    } catch error {
        print(error.kind)
        print(error.message)
    }
}
```

Supported targets: int, finite float64, bool, string, json, arrays, string-keyed maps,
Option, tagged enums and declared records (including qualified imported records and recursive schemas with finite
input). Record visibility is checked. Required record fields must be present; unknown fields fail. Optional fields decode
missing values and null to None, with explicit handling in the program. JSON null
is also valid for json. There are no implicit string-to-number conversions. Tagged
enum envelopes and nested Option rules are documented in [enums](enums.md).
An int must be mathematically integral and fit signed 64 bits; exponent notation
is accepted when exact. Error is reserved for caught runtime data failures. Function values cannot be
decoded or encoded as JSON.

Failures use the existing data error path and can be caught as Error. Conversion
messages identify a path such as `$[0].amount`; structural JSON parsing errors retain
the parser diagnostic. Collection results are mutable copies; json fields retain
immutable subtrees. Partial allocations stay rooted during GC and never escape a
failed conversion. Existing JSON nesting/node and VM heap limits apply.

JSON parsing and serialization allow at most 1,000,000 characters and 100,000
value nodes, with the root at depth zero and a maximum depth of 128. Each scalar,
array and object counts as one node. Object member names label values and do not
add nodes or nesting depth; they must still contain valid Unicode scalar values.
For example, `{"a":1,"b":2}` contains three nodes: the object and its two numbers.
Parsing and serialization use the same counting rule, so member names cannot
make otherwise valid serialized data exceed the reader's structural limit.

The target type is carried by executable pixels and the JSON_DECODE instruction,
not metadata. Recovery reconstructs the typed expression. Timeline replay repeats
this deterministic conversion without file I/O. Tests cover independent payloads,
exact numbers, missing/extra fields, wrong types, GC, images and recording restore.
