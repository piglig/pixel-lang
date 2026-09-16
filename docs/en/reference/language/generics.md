# Generic records and functions (language 0.8)

[中文](../../../zh-CN/reference/language/generics.md) · [English](generics.md) · [Documentation](../../README.md)

Declare type parameters explicitly: `record Box[T] { value: T }`. Parameters are
scoped to their declaration; two records can independently declare a parameter T.
Type arguments are explicit at construction and in annotations: `Box[int]`,
`Box[float64]{value: 1.25}`. Multiple arguments use `Pair[int, string]`.

Arguments may be nested containers or other instantiated records. Parameters can
appear anywhere within field types, e.g. `record Batch[T] { items: [T] }` and
`record Node[T] { children: [Node[T]] }`. Ordinary recursive structures are legal;
recursion that expands types without bound is rejected by type complexity or
instantiation limits. A program admits at most 256 concrete record instances and
32 type parameters per declaration. Types retain the structural depth/node limits
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

Declare explicit parameters with `fn identity[T](../../../reference/language/value: T) -> T = value` and call
with `identity[int](../../../reference/language/42)`. Qualified calls use `module.identity[int](../../../reference/language/42)`. Ordinary
functions reject type arguments; explicit generic calls require exactly the declared count.
There is no implicit numeric conversion or dynamic fallback during instantiation.

Definitions are checked with abstract type parameters, even when unused. Operations
on T must be supported by that abstract type: passing/returning values, containers,
indexing and compatible generic calls are supported. Arithmetic, scalar conversion
and equality on unconstrained T are rejected. Numeric/interface constraints are not
currently supported. Intrinsic-specific checks, such as JSON decoding to Error, are
also enforced on each concrete specialization.

Each used concrete argument tuple creates one checked function instance. Repeated
calls and ordinary generic recursion reuse the instance. Expanding instantiations
are bounded by type complexity and a limit of 256 generic function instances.
The template and explicit type arguments remain in semantic pixels; bytecode emits
concrete signatures and bodies only. There is no unresolved T in the VM.

Type arguments are access-checked in the caller. A private caller-owned record can
be passed to a public generic function such as collections.Copy[T]; substitution
does not require exporting it. The template itself cannot name inaccessible types
or access fields on an unconstrained T. These permissions are derived again when
compiling pixels, independently of metadata.

Studio links generic calls to the declaration, keeps type parameter renames scoped,
and displays instantiated stack frames such as identity[int] with their source
parameter names. Breakpoints and reverse execution retain the template's source
locations. The standard library supplies shallow Copy[T] and Repeat[T].

Tagged enum templates and Option now have core execution support; see [enums](enums.md).
Function values and captures now execute through pixels; see [functions](functions.md).

## Argument inference (language 0.8)

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
argument. Tests in test_inference_v7.py cover cross-module private types, callbacks,
containers, recursion, ambiguity, executable PNG recovery and every-state replay.
