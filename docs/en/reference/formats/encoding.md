# Semantic-pixel encoding

[简体中文](../../../zh-CN/reference/formats/encoding.md) · [English](encoding.md) · [Documentation](../../README.md)

Language, encoding and format versions are 0.9. Colors encode tokens; explicit regions and links encode structure. The physical PNG container has a separate specification.


**On this page**

- [Contract and interface](#contract-and-interface)
- [Native model encoding](#native-model-encoding)
- [Value-block encoding](#value-block-encoding)
- [Further reading](#further-reading)

## Contract and interface

A=0 denotes an empty cell; A=255 a semantic cell. Partial alpha and unknown colors
fail. Payload N=256*G+B is interpreted by semantic category, never as a VM opcode.

| R | Meaning |
|---|---|
|16|signed 16-bit literal|
|17|boolean payload 0/1|
|18|signed 64-bit literal head followed by four big-endian words|
|19|UTF-8 byte length followed by big-endian byte-pair words|
|20|finite binary64 literal head followed by four big-endian words|
|32|unsigned 16-bit identifier|
|48|operator table index|
|64|keyword table index|
|80|punctuation table index|
|96|type table index|
|112|16-bit literal continuation word|
|128|builtin table index|

The current encoding tables are OPS/KEYWORDS/PUNCT/TYPES and BUILTINS in
pixellang/codec.py and typesys.py. Literal reconstruction is parser-owned; UTF-8,
lengths and integer heads are validated. var and entry are executable keywords.
Two-binding for uses a comma; jsonDecode carries an encoded target type.

All language/encoding/file axes are 0.9; other versions are rejected. Display
palette transformation in picture.py is losslessly reversible and separate from
semantic RGBA. Exact grid coordinates and graph edges still participate in meaning.

Generic parameter/argument types, tagged enum declarations and constructors,
exhaustive match cases, and anonymous-function body references are executable
tokens. Lambda bodies are separate spatial regions reconstructed into lexical ASTs.
Capture requirements are derived from those bodies; see [functions](../language/functions.md) and [enums](../language/enums.md).

## Native model encoding

This section describes expression-flow encoding used by the current compiler.
The physical tables in `pixellang/codec.py` must match `selfhost/pixel_tokens.pxl`.
The host interprets physical colors; PixelLang handles parsing, checking and recovery.
Executable meaning never depends on source text or readable names in image metadata.

A frame stores nominal coordinate-system identity and a length unit. Point3, Vec3,
Transform3, Entity, Space, Link and Flow retain complete type arguments. Imported frames
retain module/declaration identities. XYZ does not implicitly encode logical time.

### Expression-flow regions

A flow header stores declaration identity, creation parameters, step parameters and result type.
Child regions decode directly to native AST:

| Keyword | Encoded content | AST |
| --- | --- | --- |
| let | Binding identity, type and ordinary expression | flow_value |
| state | Binding identity, type and initializer expression | flow_state |
| instance | Binding identity and direct constructor call | flow_instance |
| next | State binding identity and ordinary expression | flow_next |
| output | Ordinary expression | flow_output |

Named computations, states and children use ordinary lexical identities, with no separate
graph-local identity range. Child `.step(...)` uses ordinary field/call encoding. The recovered
checker verifies ownership, arguments, activation counts and instance dependencies. Binding
reconstructs dependency edges from expressions rather than auxiliary graph metadata.
The emitter writes inferred value/state types explicitly; the parser also accepts omitted types.
Optional names improve readability only. Identity must survive their removal.

The node/connect tokens and parsing paths are removed. State, instance, next and output
outside flow regions are rejected. A let inside an ordinary function remains a local declaration.

### Functions and access permissions

Functions use fn. The effect_fn, effect_function and effect_body tokens encode declarations,
function types and separate anonymous bodies with external effects, recovering as `effect fn`.
Both function kinds share parameter, result and closure structure. Recovery derives captures
from lexical binding; images do not store runtime captured values. Component declarations
and function types are removed.

Records, containers and enums encode ordinary value types. There are no built-in mut,
Frozen, freeze or thaw tokens. The inout type token has index 25 (`[96, 0, 25, 255]`),
followed by a parenthesized value type, and is valid only in parameter positions.
The call-site & token has punctuation index 18, followed by a writeback path.
Recovery preserves both markers and reruns native type/access/exclusivity checks.

### Declaration contracts and record updates

A colon followed by Equatable, Ordered, Numeric or Hashable after a generic parameter
identity encodes its capability constraint. It recovers as type_constraint for checking,
not as an ordinary value type.

The with keyword has index 35. Encode a parenthesized base, then `with {`, UTF-8 field names
and replacement expressions in source order, and `}`. Decoding produces record_update.
Shorthand fields encode their bound expressions; unspecified fields retain the base value.

## Value-block encoding

A `value_block(id)` expression references a `value_body id` root region in the same module.
Ordinary statement child regions form the body; a `block_result` child represents its final value
and must be the last statement. Recovery constructs `value_block`/`block_result` AST nodes directly,
without a function or closure and without changing lexical ownership.
Each body is referenced once. Missing, duplicate, cyclic and unused bodies, and mismatched reference
kinds, must be rejected. These semantics reside in pixels, not attached source, AST or bytecode.

The current compiler writes and recovers these regions directly; see [enums and match](../language/enums.md) for value-block semantics.

## Further reading

- [Documentation index](../../README.md)
