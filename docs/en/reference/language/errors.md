# Result and error propagation

[简体中文](../../../zh-CN/reference/language/errors.md) · [English](errors.md) · [Documentation](../../README.md)

Recoverable failures return Result. Callers handle or propagate them explicitly; execution faults and cancellation terminate execution.


**On this page**

- [Contract and interface](#contract-and-interface)
- [Typed business results](#typed-business-results)
- [Must-use results](#must-use-results)
- [Explicit propagation](#explicit-propagation)
- [Checked data operations](#checked-data-operations)
- [Further reading](#further-reading)

## Contract and interface

Recoverable failures use `Result[T, E]`, absence uses [Option](enums.md), and `?` propagates failure explicitly.
The language has no `try/catch` or built-in `fail`. Recoverable failures and
execution termination follow the distinct rules below.

Data and I/O operations return `Result[T, Error]`. Match Err to read its structured Error fields:

| Field | Meaning |
| --- | --- |
| kind | Category: json, io, numeric, collection, text, bytes, value or user |
| code | Stable dotted code used for program decisions |
| message | Human-readable diagnostic; wording is not a branching contract |
| path | JSON path, requested relative file path, collection key/index, or empty |
| operation | Operation that failed, e.g. jsonDecode, read, write or parseInt |
| expected | Expected type/value description when applicable, otherwise empty |
| actual | Actual type/value description when applicable, otherwise empty |

JSON codes include json.invalid_syntax, json.duplicate_key, json.type_mismatch,
json.missing_field, json.unknown_field, json.numeric_range, json.missing_key,
json.index_bounds, json.structure_limit and json.cycle. JSON paths start at $;
identifier fields use .name, other object keys use ["quoted.key"], arrays use [i].
Missing-field paths identify the absent field. JSON parsing failures use $ when
called through jsonDecode. Context survives nested conversion and is attached once.

File codes include io.access_denied, io.not_found, io.invalid_path, io.invalid_file,
io.too_large, io.invalid_encoding, io.no_space, io.unsupported_host and io.failure.
Classification uses explicit capability/path checks and OS error codes, not message
matching. File observations record the structured error and replay it without
re-accessing the filesystem, even when the external state has changed.

Numeric parsing uses codes such as numeric.invalid_text and numeric.range.
Direct indexing out of bounds, division by zero and invalid collection operations are execution faults.
Use an Option-returning lookup when absence is expected. Business results can carry a domain enum
as E without constructing or throwing exceptions.

Error message, expected and actual are bounded to 256 characters; path is bounded
to 1024 and operation to 64. File recording budgets account for structured error
payloads. Assertions, arithmetic overflow, VM limits and cancellation remain fatal.
A diagnostic phase is separate from the Error.kind category.

## Typed business results

The new implementation uses the built-in type `Result[T, E]`, without an import, for expected
business failures. `Ok(value: T)` carries success and `Err(error: E)` carries a
domain error. E may be an enum with typed payloads, such as expected and actual
payment amounts, without parsing human-readable messages.

```pixellang
enum PaymentError { AmountMismatch(expected: int, actual: int), AlreadyPaid }
fn Pay(amount: int) -> Result[int, PaymentError] {
    if amount != 100 {
        return .Err(PaymentError.AmountMismatch(100, amount))
    }
    return .Ok(amount)
}
```

Callers match Ok and Err, then exhaustively match the domain error enum. Missing
match branches and incorrect payload types are rejected. Result has a built-in
must-use contract, so its values cannot be silently discarded as expression
statements. It uses native typing, equality, JSON, PNG and debugging.

The bundled development compiler supports the built-in type and propagation.
Import `std/result.pxl` for the following ordinary combinators:

- `result.Map(value, transform)` transforms success while preserving the error type.
- `result.MapError(value, transform)` transforms errors while preserving the success type.
- `result.AndThen(value, next)` invokes the next computation only for success; next returns a Result with the same error type.

These are ordinary pure functions with `fn` callbacks. Unselected callbacks do
not run. Results follow value semantics: later local updates do not modify inputs. They do
not catch runtime exceptions or automatically turn messages into domain errors.

Err is a normal returned value: it does not automatically trigger a
flow rollback. A state machine explicitly chooses whether to retain its old state
or produce a new one, and exposes business rejection as output. Cancellation,
resource limits and fatal faults retain their runtime rules. Handle expected
rejection separately from execution failure.

## Must-use results

Built-in `Result[T, E]` cannot be discarded directly as an expression statement.
Ordinary records and enums carry no additional must-use annotation.
Bind, return, pass or match a result. Use `result.Ignore(Pay(...))` to explicitly
choose to ignore a business outcome; Pay still executes first.
This checks expression statements, not linear ownership or whole-program unused
variables: an unread binding is legal, and containers do not inherit the rule from
their elements. Ignore does not catch execution faults.
PNG preserves the Result type; recovered source undergoes the same checks without
relying on name metadata.

## Explicit propagation

`value?` evaluates its operand once. Ok/Some unwrap the success value; Err/None
return from the current function. Success types may change, but Result error
types must match. Convert errors explicitly with MapError before propagation.
Propagation cannot cross a closure boundary and does not undo earlier inout writes.

```pixellang
fn Receipt(amount: int) -> Result[string, PaymentError] {
    let paid = Pay(amount)?
    return .Ok("paid " + string(paid))
}
```

Functions declare their Result or Option return type; local closures can also
receive that type from context. A flow handles results through ordinary functions
and explicitly chooses next and output. Business failure does not imply rollback.

## Checked data operations

The bundled development compiler returns `Result[T, Error]` for numeric parsing,
UTF-8 decoding, float-byte decoding, compression/decompression, JSON parsing/access/type checks,
and file operations. `jsonDecode::[T](text)` returns `Result[T, Error]`, including when
T is itself a Result. Match the result or propagate it inside a Result-returning function.

```pixellang
fn AddOne(text: string) -> Result[int, Error] = .Ok(parseInt(text)? + 1)
```

Only expected data/I/O failures become Err. Cancellation, resource exhaustion,
invalid bytecode and effect violations remain execution failures. This boundary
does not add automatic rollback to prior writes. Byte codecs report malformed data
through `Error` values; for example, `zlibCompress([256])` returns `Err` with code
`bytes.range` and operation `zlibCompress`. Match that result or propagate it with
`?`; a codec failure does not invalidate or consume its input array.

## Further reading

- [Documentation index](../../README.md)
- [Language overview](overview.md)
