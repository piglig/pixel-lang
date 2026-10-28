# Functions, effects and closures

[简体中文](../../../zh-CN/reference/language/functions.md) · [English](functions.md) · [Documentation](../../README.md)

Function signatures describe parameters, results, update permissions and external effects. Closures capture creation-time values; calls follow a defined evaluation order.


**On this page**

- [Contract and interface](#contract-and-interface)
- [Function types and permissions](#function-types-and-permissions)
- [Lambdas and value captures](#lambdas-and-value-captures)
- [Evaluation, delivery, and debugging](#evaluation-delivery-and-debugging)
- [Further reading](#further-reading)

## Contract and interface

The development compiler uses one `fn` family. Plain functions have no external
effects; I/O and runtime instance creation or advancement require `effect fn`.
Legacy `component` declarations and function types are rejected.

```pixellang
fn Twice(value: int) -> int = value * 2
effect fn main() { print(Twice(21)) }
```

## Function types and permissions

Write function types as `fn(T, U) -> R`. A unit return may omit the arrow, as in
`fn(int)`. Effect-permitting function types use `effect fn(T) -> R`. Parameter
names do not affect type identity. `effect` is a modifier only immediately before
`fn`; it remains available as an ordinary identifier elsewhere.

Functions without external effects can satisfy effect-permitting callback types,
not vice versa; `inout` access modes must still match.
Parameters are contravariant and results covariant. Containers and nominal type
arguments do not acquire implicit conversions through that rule.

```pixellang
fn Apply(value: int, operation: fn(int) -> int) -> int = operation(value)
effect fn Report(value: int) { print(value) }
effect fn Visit(value: int, operation: effect fn(int)) { operation(value) }
effect fn main() { Visit(Apply(3, Twice), Report) }
```

The declared contract governs calls, even when an effectful function currently has
an empty body. An effectful caller cannot grant I/O permission to a callee without `effect`.
Variables, record callbacks, generics, and dynamic calls retain effect information.
The runtime also enforces call boundaries.

Effect violations are diagnosed at the definition that breaks its contract, without inferring implementation effects into callers. Calls use the expression's static function type: even `let alias: effect fn() = pure` requires effect permission when called. Successfully checked functions with unchanged semantic dependencies reuse effect validation.

Local `var` updates are not external effects. Explicit writeback uses a separate
parameter access permission:

```pixellang
fn Increment(value: inout int) { value += 1 }
effect fn main() {
    var count = 0
    Increment(&count)
    print(count)
}
```

Function types retain the mode, for example `fn(inout int)`. Both the parameter and
call argument must declare access explicitly. Access cannot escape or be captured.
Overlapping writes through the same root variable are rejected; separate variables
can be written independently. `&` is restricted to call arguments, not a reference value.

`fn` guarantees no external effects, not the absence of caller-visible changes.
A function with `inout` parameters writes back to its arguments. The absence of
`effect` alone does not justify memoizing, merging, or reordering calls. Local value
updates leave caller values unchanged; explicit parameter updates must be preserved.

## Lambdas and value captures

Lambdas use `fn(x: T) -> R { ... }` or `fn(x: T) = expression`. Context may provide
parameter types, as in `fn(x) = x + 1`; insufficient evidence is an error. Effectful
lambdas explicitly use `effect fn(...)`.

Every closure captures the values present at creation, and captures are readonly.
Later outer-variable updates do not alter existing closures:

```pixellang
effect fn main() {
    var offset = 2
    let add = fn(value: int) -> int = value + offset
    offset = 10
    print(add(3)) // 5
}
```

Nested closures and individual loop iterations follow the same rule. Use flow for
persistent instance state. Captures do not provide hidden shared mutation. Ordinary
value captures need no permission conversion; inout access cannot be captured.
Implementation progress is tracked in the [design principles](../../internals/design.md).

## Evaluation, delivery, and debugging

Evaluate the callable first, then each argument once in source order. Replacing
the variable that held the callable while evaluating arguments does not replace
the target already selected for this call.

PNG preserves function types, effects, lambda body identities, and capture relationships.
Recovered source rechecks the same contracts, including effect permissions on its entry
wrapper. Bytecode uses `CAPTURE_VALUE` to create readonly capture cells; legacy `CAPTURE`
is rejected. Checkpoints and replay preserve captured values rather than rereading
outer bindings to reconstruct a closure.

Relevant tests include `test_function_effect_runtime.py`, `test_native_function_effects.py`,
`test_value_captures.py`, `test_native_value_captures.py`, and `test_native_inout_png.py`.

## Further reading

- [Documentation index](../../README.md)
- [Language overview](overview.md)
