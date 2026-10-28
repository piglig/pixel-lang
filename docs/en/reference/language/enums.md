# Enums, Option and pattern matching

[简体中文](../../../zh-CN/reference/language/enums.md) · [English](enums.md) · [Documentation](../../README.md)

Enums express finite typed alternatives and Option expresses absence. match checks coverage and can compute results through value blocks.


**On this page**

- [Contract and interface](#contract-and-interface)
- [Absence](#absence)
- [Contextual variants](#contextual-variants)
- [Product patterns and guards](#product-patterns-and-guards)
- [Value blocks and control flow](#value-blocks-and-control-flow)
- [JSON](#json)
- [Execution and evidence](#execution-and-evidence)
- [Further reading](#further-reading)

## Contract and interface

Enums are nominal types with a fixed set of variants and typed payloads:

```go
enum Event[T] { Stop, Data(value: T) }
let event = Event::[int].Data(42)
let answer = match event {
    Event.Stop => 0,
    Event.Data(value) => value
}
```

Constructors are qualified by the enum type. Generic arguments can be supplied explicitly or by an expected enum type.
Provide a type annotation or explicit arguments when type evidence is insufficient. Zero-payload variants use `Event::[int].Stop`, without parentheses. Variant names and payload field names are unique within their
respective declaration scopes. Exported enums can be constructed and matched through
an imported module alias. Patterns may omit generic arguments when the subject
supplies them; explicit arguments must match the subject exactly.

Match evaluates its subject once. Only the selected arm executes. Expression arms
must produce the same type; a surrounding expected type is supplied independently
to each arm. Match expressions may also be used as statements. Pattern bindings are immutable and
scoped to that arm. Payloads retain their declared ordinary type T without a Frozen projection. Assign a payload to a local var to update a copy; those updates do not change the original enum. Enum payloads are accessed through patterns, not field selection.

Every possible input must be covered; `_` matches any value. Fully shadowed
patterns, wrong payload arity, unknown variants and missing cases are compile errors. A statement match returns on all paths when every arm
does. Loop control follows the lexical loop scope; Result propagation follows the enclosing function contract.

## Absence

`Option[T]` is the built-in enum with `Some(value: T)` and `None` variants. `Option.Some(42)`
infers Option[int]. `.None` requires an expected Option type, such as
`let value: Option[int] = .None`. Option follows the same qualified and contextual
variant rules as user enums; bare `Some` and `None` are ordinary user names. Explicit constructors `Option::[int].None` and
`Option::[int].Some(42)` work without context. Ordinary T never implicitly contains
absence, and there is no implicit conversion from T to Option[T].

`values.get(key)` on `map[K, T]` returns `Option[T]`: Some for a present key, None otherwise. Extracted values follow the same value isolation rules and need no freezing or thawing.
Ordinary map indexing retains its checked missing-key error behavior.

## Contextual variants

An expected enum type allows omission of the type qualifier:

```pixellang
enum Status { Waiting, Paid(amount: int) }
let status: Status = .Paid(100)
let amount = match status {
    .Waiting => 0,
    .Paid(value) => value
}
```

Expected types come from annotations, parameters, return types or record fields; patterns use the subject type.
Generic calls may also obtain evidence from other arguments. `let status = .Waiting` without an expected type
is rejected: the compiler never searches globally for same-name variants. Use `Status.Waiting` when needed.
PNG preserves the resolved nominal type; recovered source may use qualified names with identical semantics.

## Product patterns and guards

The bundled development compiler supports product patterns and guards.
See the [design principles](../../internals/design.md) for full release status.

```pixellang
enum Status { Waiting, Paid(amount: Option[int]) }
enum Event { Pay, Cancel }
fn choose(status: Status, event: Event) -> int = match (status, event) {
    (.Waiting, .Pay) => 1,
    (.Paid(.Some(amount)), .Pay) if amount > 0 => amount,
    (.Paid(.Some(amount)), .Pay) => 0,
    (_, _) => -1
}
```

`match (a, b)` matches multiple inputs. Each arm must have one pattern per input.
These parentheses form a match input list; they do not introduce general tuple values.
All inputs evaluate once, left to right. Enum payload patterns can nest recursively.
A plain name binds the whole value at that position; `_` ignores it. Bindings are
readonly and visible in that arm's guard and result.

Arms are selected in source order. An `if` guard runs only after all patterns match;
it must return bool. A false guard proceeds to the next arm. Guarded arms never
establish exhaustive coverage, even with `if true`. Repeating an outer variant is
valid: an arm is unreachable only when earlier unguarded arms cover its entire
pattern combination. PNG stores each pattern's nominal type, binding identity and
guard expression; source recovery does not rely on hidden source text.

## Value blocks and control flow

Match arms can compute their results with value blocks.

```pixellang
fn choose(value: Option[int]) -> int = match value {
    .Some(n) => { let adjusted = n + 1; adjusted },
    .None => 0
}
```

The final expression without a semicolon supplies the block value. A final
semicolon discards that value; an empty block has type `unit`. Local bindings stay
inside the block. Arms that complete normally must agree on their result type.

Value blocks compute results. They cannot use `return` to leave the enclosing
function and do not inherit an outer loop's break/continue target. Put early
returns in surrounding statements using if or statement match. Loops declared
inside a value block may break or continue; nested functions own their return
and loop contexts.

`?` still propagates failure under the enclosing function's Option/Result
contract; a value block creates no hidden function. Propagation skips later
operands and releases abandoned temporary accesses without rolling back effects
or writes that already happened.

## JSON

For Option[T], JSON null decodes to None; other JSON values decode through T and
produce Some. A missing Option field in a record also becomes None. Required record
fields remain required, and unknown fields remain errors. Encoding None produces
null; encoding Some produces its payload. This intentionally does not preserve the
distinction between a missing record field and an explicit null field.

Adjacent Option[Option[T]] cannot be decoded because null cannot distinguish None
from Some(None). Encoding that type raises json.unsupported_type. An intervening
container is supported, e.g. Option[[Option[int]]] can encode `[null, 3]`.

Custom enums use an explicit JSON object, for example:

```json
{"variant": "Data", "fields": {"value": 42}}
```

Both envelope keys are required, and extra keys are rejected. Variant payload fields
must match the declaration exactly. Unknown variant tags produce
json.unknown_variant; payload conversion errors retain their nested field path.

## Execution and evidence

Enum declarations, generic arguments, constructors and match patterns are encoded
in semantic pixels. Concrete enum schemas and checked enum operations enter the VM;
no hidden text metadata is needed for executable PNGs. Payload references are heap
roots through their enum object and participate in deterministic replay.

`tests/test_enums.py` covers construction, negative checking, generic modules,
JSON, branch effects, loop control, PNG recovery and every intermediate replay
state. `tests/test_debugvalues.py` covers variant summaries, paged payloads and
pattern binding names. `tests/test_enum_editor.py` covers cross-module type/variant navigation and
rename, scoped pattern bindings, collision rejection, generic constructor completion,
incomplete type annotations and payload member completion. Real development-host
VS Code integration also verifies variant completion, definition and rename.

## Further reading

- [Documentation index](../../README.md)
- [Language overview](overview.md)
