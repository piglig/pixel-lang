# RGBA encoding 0.8

[中文](../../../zh-CN/reference/formats/encoding.md) · [English](encoding.md) · [Documentation](../../README.md)

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

All language/encoding/file axes are 0.8; other versions are rejected. Display
palette transformation in picture.py is losslessly reversible and separate from
semantic RGBA. Exact grid coordinates and graph edges still participate in meaning.

Generic parameter/argument types, tagged enum declarations and constructors,
exhaustive match cases, and anonymous-function body references are executable
tokens. Lambda bodies are separate spatial regions reconstructed into lexical ASTs.
Capture requirements are derived from those bodies; see [functions](../language/functions.md) and [enums](../language/enums.md).
