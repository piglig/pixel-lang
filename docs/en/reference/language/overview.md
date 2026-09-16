# PixelLang language reference (0.8)

[中文](../../../zh-CN/reference/language/overview.md) · [English](overview.md) · [Documentation](../../README.md)

This reference defines the current language behavior.

This is a self-contained language, not a Go/Java compatibility frontend. UTF-8
text and semantic pixels are authoring representations. Both compile to PixelVM
bytecode; text compilation does not require a PNG roundtrip.

## Declarations and types

`fn name(arg: T) -> R { ... }` declares a value-returning function. A unit function
omits `-> R`. `fn square(n: int) -> int = n * n` is an expression function.
Modules contain only fn, record, enum and import declarations. The selected entry module
must define parameterless `fn main()` returning unit. Imported main functions do
not run. Imports are relative paths, or bundled `std/...`, with optional `as alias`.
Only explicitly exported fn, record and enum declarations are visible across module boundaries.

Types are int (checked signed 64-bit), float64 (finite binary64), bool, string (Unicode scalar sequence),
`[T]`, `map[T]` (string keys), declared records and enums, Option[T], json, and built-in Error. Internal
representations use str, T[], T{} and nominal record IDs. There are no implicit conversions or null references. Function values and lexical closures are described in
[functions](functions.md). Generic records use explicit parameters and
arguments, e.g. `record Box[T] { value: T }` and `Box[int]{value: 42}`; see
[generics](generics.md). Generic functions use `fn identity[T](../../../reference/language/value: T) -> T`
and calls such as `identity(42)` with inferred arguments or `identity[int](../../../reference/language/42)`
with explicit arguments. Inference requires consistent static evidence from call arguments and expected
results; unresolved or conflicting evidence is diagnosed. Anonymous functions may
use `fn(x) = expression` or a block, with contextual parameter types and inferred
results. Named function interfaces remain explicitly typed. JSON null remains
a valid tagged json value. Record types are nominal, not structural.

`let x = expression` infers a binding type; `let x: T = expression` declares it.
Use var for reassignment. Parameters, for bindings and catch bindings are immutable.
Immutability applies to bindings: elements/fields of arrays/maps/records remain
mutable. Error fields are read-only. Empty containers require contextual or explicit
types, e.g. `let values: [int] = []`, `map[int]{}`, or `([]: [int])`.

A record is `record Sale { category: string, amount: int }`. Construct it with
`Sale{category: "books", amount: 42}`. `Sale{category, amount}` reads the same-named
local bindings. Every field must be supplied exactly once with the correct type.
Field access uses dot; arrays/maps use brackets. Aliases share mutable objects.

## Expressions and statements

Precedence from high to low: member/index/call; unary - and !; * / %; + -;
< <= > >=; == !=; &&; ||. Binary operators associate left. && and || short-circuit.
String + concatenates. Order comparisons accept matching int, float64 or string values;
comparison and equality do not coerce types. Numeric operators accept matching
int or float64 operands; see [numbers](numbers.md) for numeric rules.
Mutable containers, records, enums, functions and json are not equality-comparable.
Integer division truncates toward zero; float64 division follows [numbers](numbers.md).

Use `if condition { ... } else { ... }`, `while condition { ... }`, or
`for value in collection { ... }`. With two bindings, arrays/strings supply a
zero-based Unicode index and value; maps supply key and value. One-binding map
iteration supplies keys. Iteration takes a shallow snapshot once, preserving map
insertion order and values at loop entry. Nested objects still share references.

break/continue target the nearest loop and correctly unwind handlers entered
inside that loop. A for continue advances its snapshot index. Empty blocks are
valid. Record constructors directly in control headers require parentheses to
distinguish their brace from the statement body.

`try { ... } catch error { ... }` catches data failures. Error exposes read-only
kind, code, message, path, operation, expected and actual strings, as specified in
[errors](errors.md). Uncaught data failures stop execution. Assertions,
arithmetic overflow, VM budgets and cancellation are fatal. `return` in a unit
function has no value; every path in a value function must return. Loops are
conservatively treated as possibly terminating. Directly unreachable statements
are rejected. There are no module variables or nested named function declarations. Anonymous
functions capture lexical bindings; see [functions](functions.md).

Assignment is `x = value`, `items[i] = value`, or `item.field = value`. All writable
locations support compound updates for the corresponding operand types. Receivers
and indices evaluate once, left to right; a compound update reads the old value
before evaluating the right operand and then writes the result. A failed read skips
the right operand; earlier side effects are not rolled back. ++/-- are integer
updates. Print is `print(value)`; standalone calls discard their value.

`jsonDecode[T](../../../reference/language/text-or-json)` follows [typed JSON rules](json.md). Other
built-ins provide collection operations, text processing, input(), assertions,
JSON inspection and granted text-file I/O. Signatures are centralized in
`selfhost/checker_builtins.pxl` for the self-hosted frontend, with host reference
signatures in `pixellang/typesys.py`; library APIs are the exported declarations
in `pixellang/stdlib`.

## Semantic pixels

Text identifiers resolve to numeric IDs. Rows encode declarations/statements,
including separate var, entry, break and continue keywords, two for bindings and
JSON decode target types. Empty branches are representable; catch presence is
independent of body length. Metadata names do not decide execution or entry.
Read [spatial semantics](spatial.md) and [encoding](../formats/encoding.md).

## Enums and absence

Tagged enums, Option[T], constructors and exhaustive expression/statement match are
defined in [enums](enums.md), including JSON representation and current tooling
limits. Payload bindings are immutable; match subjects are evaluated once.
