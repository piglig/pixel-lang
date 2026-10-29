# Language overview

[简体中文](../../../zh-CN/reference/language/overview.md) · [English](overview.md) · [Documentation](../../README.md)

This page surveys declarations, types, expressions and modules. Run the tutorial first, then use the linked topics for exact contracts.


**On this page**

- [Declarations and types](#declarations-and-types)
- [Expressions and statements](#expressions-and-statements)
- [Semantic pixels](#semantic-pixels)
- [Enums and absence](#enums-and-absence)
- [Further reading](#further-reading)

## Declarations and types

`fn name(arg: T) -> R { ... }` declares a value-returning function. A unit function
omits `-> R`. `fn square(n: int) -> int = n * n` is an expression function.
Modules contain declarations rather than top-level executable statements, including fn, record, enum and import.
The native spatial model implements `frame` and `flow` in the development preview; see [native 3D space](../../guides/spatial-programming.md) for usage and artifact requirements. The selected entry module
must define a parameterless main returning unit; use `effect fn main()` when external effects are needed. Imported main functions do
not run. Imports are relative paths, or bundled `std/...`, with optional `as alias`.
Cross-module declarations require explicit export, including functions, records, enums, coordinate frames and flows.

Types are int (checked signed 64-bit), float64 (finite binary64), bool, string (Unicode scalar sequence),
`[T]`, `map[K, V]`, declared records and enums, Option[T], json, and built-in Error.
There are no implicit conversions or null references; JSON null is a valid json value.
Record types are nominal: separate declarations remain different types even with identical fields.
Values of the same record type compare by field contents when all fields are comparable; see
[data semantics](data.md). Function values and lexical closures are described in
[functions](functions.md). Generic records declare parameters explicitly, e.g. `record Box[T] { value: T }`.
Construct with inferred arguments using `Box{value: 42}` or explicit arguments using `Box::[int]{value: 42}`; see
[generics](generics.md). Generic functions use `fn identity[T](value: T) -> T`
and calls such as `identity(42)` with inferred arguments or `identity::[int](42)`
with explicit arguments. Inference requires consistent static evidence from call arguments and expected
results; unresolved or conflicting evidence is diagnosed. Anonymous functions may
use `fn(x) = expression` or a block, with contextual parameter types and inferred
results. Named function interfaces remain explicitly typed. JSON null remains
a valid tagged json value. Record types are nominal, not structural.

`let x = expression` infers a binding type; `let x: T = expression` declares it.
Use var for reassignment and local field or element updates. Ordinary parameters,
for bindings, and pattern bindings are readonly. Records and containers have value
semantics: updating one local copy preserves the others. Caller writeback requires
explicit `inout` access. Empty containers require contextual or explicit
types, e.g. `let values: [int] = []`, `map[string, int]{}`, or `([]: [int])`.

A record is `record Sale { category: string, amount: int }`. Construct it with
`Sale{category: "books", amount: 42}`. `Sale{category, amount}` reads the same-named
local bindings. Every field must be supplied exactly once with the correct type.
Field access uses dot; arrays/maps use brackets. `sale with { amount: 50 }` creates
a new record with other fields preserved. Assignment and arguments do not introduce
shared mutation. See [data semantics](data.md).

## Expressions and statements

Precedence from high to low: member/index/call; unary - and !; * / %; + -;
< <= > >=; == !=; &&; ||. Binary operators associate left. && and || short-circuit.
String + concatenates. Order comparisons accept matching int, float64 or string values;
comparison and equality do not coerce types. Numeric operators accept matching
int or float64 operands; see [numbers](numbers.md) for numeric rules.
Matching scalar, Point3/Vec3 and EntityId/LinkId types support value equality, as do arrays, maps,
records and enums whose content types are comparable. Functions and json do not
support direct equality. See [data semantics](data.md) for cycle and identity rules.
Integer division truncates toward zero; float64 division follows [numbers](numbers.md).

Use `if condition { ... } else { ... }`, `while condition { ... }`, or
`for value in collection { ... }`. With two bindings, arrays/strings supply a
zero-based Unicode index and value; maps supply key and value. One-binding map
iteration supplies keys. Iteration preserves map insertion order and values at
loop entry. Updating another local copy does not change the values being iterated.

break/continue target the nearest loop. A for continue advances its snapshot index. Empty blocks are
valid. Record constructors directly in control headers require parentheses to
distinguish their brace from the statement body.

Recoverable failures use `Result[T, Error]`; missing values use `Option[T]`.
Use `match` to handle outcomes and `?` to propagate an error or missing value from a
compatible function. Built-in Result values must be used. Error carries kind, code,
message, path, operation, expected and actual strings; see [errors](errors.md).
Assertions, arithmetic overflow, VM budgets and cancellation terminate execution.
There is no try/catch/fail channel.

`return` in a unit function has no value; every normally completing path in a value
function must return a value. Statements after return, break or continue in the same
block are unreachable. Loops are conservatively treated as possibly terminating;
break does not satisfy a function's return requirement. The checker does not infer
that an arbitrary user function always terminates execution. There are no module
variables or nested named functions. Anonymous functions capture values; see
[functions](functions.md).

Assignment is `x = value`, `items[i] = value`, or `item.field = value`. All writable
locations support compound updates for the corresponding operand types. Receivers
and indices evaluate once, left to right; a compound update reads the old value
before evaluating the right operand and then writes the result. A failed read skips
the right operand; earlier side effects are not rolled back. Integer updates use `+= 1` / `-= 1`; `++/--` are not provided. Print is `print(value)`; standalone calls may discard non-Result values; see [errors](errors.md).

`jsonDecode::[T](text-or-json)` follows [typed JSON rules](json.md). Other
built-ins provide collection operations, text processing, input(), assertions,
JSON inspection and granted text-file I/O. Signatures are centralized in
`selfhost/checker_builtins.pxl` for the self-hosted frontend, with host reference
signatures in `pixellang/typesys.py`; library APIs are the exported declarations
in `pixellang/stdlib`.

## Semantic pixels

Text identifiers resolve to numeric IDs. Rows encode declarations/statements,
including separate var, entry, break and continue keywords, two for bindings and
JSON decode target types. Empty branches are representable. Metadata names do not decide execution or entry.
Read [spatial semantics](spatial.md) and [encoding](../formats/encoding.md).

## Enums and absence

Tagged enums, Option[T], constructors and exhaustive expression/statement match are
defined in [enums](enums.md), including JSON representation and current tooling
limits. Payload bindings are immutable; match subjects are evaluated once.

## Further reading

- [Documentation index](../../README.md)
