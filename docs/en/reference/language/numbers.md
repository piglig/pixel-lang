# Numeric contract (language 0.8)

[中文](../../../zh-CN/reference/language/numbers.md) · [English](numbers.md) · [Documentation](../../README.md)

int is checked signed 64-bit. float64 is finite IEEE 754 binary64, using round to
nearest, ties to even. Gradual underflow and rounding to signed zero are permitted.
NaN and infinities are not language values. Arithmetic overflow is fatal; a rejected
external number or out-of-range explicit conversion is a catchable data error.
Both integer and float division/remainder by zero are catchable data errors.

Decimal float literals require either a fractional part or exponent: 1.0, 1e3,
1_000.25, 1.5e-2. Digits are required on both sides of a decimal point. Separators
are allowed only between digits. A preceding minus is unary negation. Negative zero
is preserved by literals, pixels, the constant pool, arithmetic, JSON and replay.
The literal 1 remains int even where a float64 is expected.

Operators + - * / % and comparisons require operands of the same type. Unary minus
accepts either numeric type. Integer division truncates toward zero. Float division
rounds the quotient to binary64. Remainder has the dividend's sign, with magnitude
smaller than the divisor; float remainder uses fmod semantics. +0.0 and -0.0 compare
equal. No approximate equality is implicit. ++/-- add/subtract integer one and thus
apply to int locations; a float update spells += 1.0 or -= 1.0.

float64(int) rounds to the nearest binary64 value; integers above 2^53 need not be
exact. int(float64) truncates toward zero and rejects the truncated result outside
[-2^63, 2^63). Same-type numeric conversion is an identity. No bool/string coercion
is permitted. parseFloat64(string) accepts a signed decimal with optional fraction
and exponent, without whitespace or separators; it rejects non-finite results.

string(float64) emits a shortest decimal that roundtrips to the same binary64,
retaining the float spelling (.0 or exponent) and negative zero. JSON decoding to
float64 rounds a JSON number to binary64 and rejects non-finite results. Decoding
to int retains exact-integral checking of the original JSON decimal. jsonFrom and
jsonStringify preserve a float's roundtrip decimal representation.

Physical encoding uses an R=20 header with reserved payload zero followed by four
16-bit data words containing the big-endian IEEE 754 bits. Reserved payloads and
non-finite bit patterns are rejected independently of authoring metadata.
