# Generics and capabilities

[简体中文](../../../zh-CN/reference/language/generics.md) · [English](generics.md) · [Documentation](../../README.md)

Generics reuse type structure; capability constraints declare permitted operations. Prefer inference and provide explicit type arguments when evidence is insufficient.


**On this page**

- [Explicit type arguments](#explicit-type-arguments)
- [Generic records](#generic-records)
- [Generic functions](#generic-functions)
- [Generic values retain their type](#generic-values-retain-their-type)
- [Argument inference](#argument-inference)
- [Capability constraints](#capability-constraints)
- [Further reading](#further-reading)

## Explicit type arguments

Value expressions use `::[T]` for explicit specialization; type annotations keep `[T]`.
For example, `let box: Box[int] = Box::[int]{value: 42}`. An ordinary
`values[index]` is always indexing, independent of declared type or function names.

## Generic records

Declare type parameters explicitly: `record Box[T] { value: T }`. Parameters are
scoped to their declaration; two records can independently declare a parameter T.
Type annotations specify arguments, such as `Box[int]` or `Pair[int, string]`.
Construction can infer them from fields or the expected type: `Box{value: 42}`
produces `Box[int]`, and `let items: Box[[int]] = Box{value: []}` uses the annotation
to type the empty array. Use `Box::[float64]{value: 1.25}` to specify arguments
explicitly. Conflicting or insufficient evidence is an error; numeric types are not implicitly converted.

Arguments may be nested containers or other instantiated records. Parameters can
appear anywhere within field types, e.g. `record Batch[T] { items: [T] }` and
`record Node[T] { children: [Node[T]] }`. Ordinary recursive structures are legal;
recursion that expands types without bound is rejected by type complexity or
instantiation limits. A program admits at most 256 concrete generic record/enum instances, including
builtin generic enums. Nongeneric declarations do not consume this instantiation
budget and remain subject to compilation resource limits. Each declaration admits
at most 32 type parameters. Types retain the structural depth/node limits
defined by typeexpr.py. Unknown types are rejected even in unused declarations.

Records remain nominal and invariant: Box[int] and Box[float64] are distinct, and
neither implicitly converts to the other. Type argument counts and substituted
field types must match. Export/access rules apply to the template and to nominal
types appearing in arguments. Type parameters do not become runtime variables.

The semantic pixel stream encodes template parameter IDs, parameter occurrences,
and concrete type arguments. A record type pixel is followed by `(module.id[args])`
when instantiated; a parameter type pixel carries `(identifier)`. Executable images
retain the template declarations. Checked IR/bytecode carry concrete record schemas;
no unresolved parameters enter the VM. JSON decoding uses the substituted schema.

Studio associates specialized field references with the template declaration and
displays the substituted field type in completion. Parameter rename is scoped to
its declaration and references. Nested generic payloads are inspectable with the
existing paged debugger; replay uses the concrete schemas reconstructed from pixels.

## Generic functions

Declare explicit parameters with `fn identity[T](value: T) -> T = value` and call
with `identity::[int](42)`. Qualified calls use `module.identity::[int](42)`. Ordinary
functions reject type arguments; explicit generic calls require exactly the declared count.
There is no implicit numeric conversion or dynamic fallback during instantiation.

Definitions are checked with abstract type parameters, even when unused. Operations
on T must be supported by that abstract type: passing/returning values, containers,
indexing and compatible generic calls are supported. Arithmetic, scalar conversion
and equality on unconstrained T are rejected. The capabilities below permit equality, ordering and numeric operations;
user-defined interface constraints are not supported. Intrinsic-specific checks, such as JSON decoding to Error, are
also enforced on each concrete specialization.

Each used concrete argument tuple creates one checked function instance. Repeated
calls and ordinary generic recursion reuse the instance. Expanding instantiations
are bounded by type complexity and a limit of 256 generic function instances.
The template and explicit type arguments remain in semantic pixels; bytecode emits
concrete signatures and bodies only. There is no unresolved T in the VM.

Type arguments are access-checked in the caller. A private caller-owned record can
be passed to a public generic function such as collections.Filter[T]; substitution
does not require exporting it. The template itself cannot name inaccessible types
or access fields on an unconstrained T. These permissions are derived again when
compiling pixels, independently of metadata.

Studio links generic calls to the declaration, keeps type parameter renames scoped,
and displays instantiated stack frames such as identity[int] with their source
parameter names. Breakpoints and reverse execution retain the template's source
locations. Ordinary assignment preserves value isolation. The standard library’s
`Repeat[T]` returns `Result[[T], Error]`; local updates preserve values held by other bindings.
See [enums](enums.md) and [functions](functions.md).

## Generic values retain their type

Reading a field of `Box[T]`, an element of `[T]`, or an enum payload declared as `T`
produces `T`. Container construction and callbacks require no snapshot type:

```pixellang
record Box[T] { value: T }
fn Read[T](box: Box[T]) -> T = box.value
fn First[T](values: [T]) -> T = values[0]
fn Singleton[T](value: T) -> [T] = [value]
```

Parameters and `let` bindings are readonly. Use `var` for local updates and `inout`
for explicit caller updates. These access rules do not change the type parameter.
Capability checks still follow the declared constraints on `T`.

Generic values follow the same [value semantics](data.md) as concrete types.

## Argument inference

Generic calls may omit their type arguments: `identity(42)` and
`collections.Filter(rows, predicate)`. The checker matches argument types against
parameter types structurally, including arrays, maps, nominal instances, Option
and full callback signatures. Repeated occurrences must agree exactly. Integer and
float64 evidence conflicts; inference never adds numeric conversions.

An untyped empty array or None can use evidence from another argument, regardless
of argument position. Calls with insufficient evidence require annotations or
explicit type arguments. Expected result types constrain generic parameters, including result-only
parameters and empty argument values. Generic function values still require
explicit specialization. Anonymous parameters can use function context; inferred
callback return types contribute generic output constraints. An unknown callback
input still requires an annotation or independent argument evidence.

Inference is repeated when compiling semantic pixels, so an inferred call needs no
hidden executable metadata. Argument evaluation remains left to right, once per
argument. Tests in test_inference.py cover cross-module private types, callbacks,
containers, recursion, ambiguity, executable PNG recovery and every-state replay.

## Capability constraints

Use `T: Equatable` to require value equality, for example:

```pixellang
fn Same[T: Equatable](left: T, right: T) -> bool = left == right
record Key[T: Equatable] { value: T }
```

The constraint permits value equality on T and on comparable containers and records
built from T. Explicit type arguments and inferred arguments must satisfy the
constraint. Forwarding a type parameter to another generic declaration requires
sufficient declared capabilities. The contract still applies when a body does not
compare its arguments or a declaration is unused. Semantic pixels and recovered
source retain constraints, which are checked again when compiling recovered code.
Unconstrained T still cannot be compared directly; see [data semantics](data.md).
Equatable, Ordered, Numeric and Hashable are supported; user-defined interface
constraints are not. Recovered source enforces the same capabilities.

### Ordering and numeric capabilities

Use `T: Ordered` and `T: Numeric` for ordering and numeric capabilities. Numeric implies Ordered, which
implies Equatable. A parameter can be forwarded to a weaker capability requirement,
but not a stronger one. Ordered supports `<`, `<=`, `>` and `>=` for int, float64
and string; it does not invent lexicographic ordering for arrays or records.
Numeric supports `+`, `-`, `*`, `/`, `%` and unary negation for int and float64,
retaining their concrete overflow and division rules. Operands still require the
same type, without implicit int/float64 mixing. Constraints do not automatically
convert integer literals to an abstract numeric type.

The standard library provides `collections.Sorted(values)` and
`collections.Sum(values, initial)`. Sorted uses stable natural ordering. Sum's
explicit initial value defines the empty result and numeric type, as in
`Sum([1, 2], 0)` or `Sum([1.5, 2.5], 0.0)`.

### Hash-key capability

`T: Hashable` is the prerequisite contract for typed map keys.
Hashable implies Equatable; Ordered and Numeric satisfy Hashable, but Hashable
does not imply Ordered. Supported types are int, finite float64, bool, string,
Point3/Vec3 and EntityId/LinkId, and enums whose payload types in every variant are hashable. Arrays,
maps, records, functions, JSON and runtime instances cannot be hash keys;
inactive enum variants are checked too. Positive and negative floating zero are
equal and identify the same map key; spatial source origins do not affect
key identity.

`map[K, V]` requires K to satisfy Hashable, including in unused declarations.
Generic map parameters therefore declare `K: Hashable`. Native source execution,
JSON round-trips, state snapshots and program PNG recovery have targeted coverage.
See [collections](collections.md) for ordering and serialization.

## Further reading

- [Documentation index](../../README.md)
- [Language overview](overview.md)
