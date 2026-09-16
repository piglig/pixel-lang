# Tagged enums, Option and match (language 0.8)

[中文](../../../zh-CN/reference/language/enums.md) · [English](enums.md) · [Documentation](../../README.md)

Enums are nominal types with a fixed set of variants and typed payloads:

```go
enum Event[T] { Stop, Data(value: T) }
let event = Event[int].Data(42)
let answer = match event {
    Event.Stop => 0,
    Event.Data(value) => value
}
```

Constructors are qualified by the enum type. Generic construction requires explicit
arguments. Zero-payload variants accept either `Event[int].Stop` or
`Event[int].Stop()`. Variant names and payload field names are unique within their
respective declaration scopes. Exported enums can be constructed and matched through
an imported module alias. Patterns may omit generic arguments when the subject
supplies them; explicit arguments must match the subject exactly.

Match evaluates its subject once. Only the selected arm executes. Expression arms
must produce the same type; a surrounding expected type is supplied independently
to each arm. Statement matches use block arms. Pattern bindings are immutable and
scoped to that arm; referenced arrays, maps and records retain their shared mutable
identity. Enum payloads are accessed through patterns, not field selection.

Every variant must be covered unless there is a final wildcard `_`. Duplicate
variants, wrong payload arity, unknown variants, unreachable wildcards and missing
variants are compile errors. A statement match returns on all paths when every arm
does. Loop break/continue and error-handler unwinding also apply inside arms.

## Absence

`Option[T]` is the built-in enum with `Some(value: T)` and `None` variants. `Some(42)`
infers Option[int]. Bare `None` requires an expected Option type, such as
`let value: Option[int] = None`. Explicit constructors `Option[int].None` and
`Option[int].Some(42)` work without context. Ordinary T never implicitly contains
absence, and there is no implicit conversion from T to Option[T].

`lookup(values, key)` returns Option[T] for a map[T]. A present key produces Some
with its existing value, preserving object identity; an absent key produces None.
Ordinary map indexing retains its checked missing-key error behavior.

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

`tests/test_enums_v6.py` covers construction, negative checking, generic modules,
JSON, branch effects, loop control, PNG recovery and every intermediate replay
state. `tests/test_debugvalues.py` covers variant summaries, paged payloads and
pattern binding names. `tests/test_enum_editor_v6.py` covers cross-module type/variant navigation and
rename, scoped pattern bindings, collision rejection, generic constructor completion,
incomplete type annotations and payload member completion. Real development-host
VS Code integration also verifies variant completion, definition and rename.
