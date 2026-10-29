# PixelVM bytecode

[简体中文](../../../zh-CN/reference/formats/bytecode.md) · [English](bytecode.md) · [Documentation](../../README.md)

The VM version is 0.9. Bytecode carries concrete types, control flow and provenance. Validate it before execution; successful compilation does not replace runtime boundary checks.


**On this page**

- [Contract and interface](#contract-and-interface)
- [Native space and dataflow](#native-space-and-dataflow)
- [Typed maps](#typed-maps)
- [Instance constructor inputs](#instance-constructor-inputs)
- [Values and checked conversions](#values-and-checked-conversions)
- [Value-path writes](#value-path-writes)
- [Expression-flow bytecode](#expression-flow-bytecode)
- [Explicit result propagation](#explicit-result-propagation)
- [Checked data operations](#checked-data-operations)
- [Operand cleanup for expression exits](#operand-cleanup-for-expression-exits)
- [Further reading](#further-reading)

## Contract and interface

The bundled compiler uses VM 0.9. This page defines the current bytecode contract; release gates are described in [verification](../../contributing/verification.md).

The VM validates and copies a PIXELVM executable with matching 0.9 language and VM
axes. Compiler version is informational. Sections are constants, nominal record
schemas (including validated built-in Error), concrete enum schemas, entry and
function bodies with explicit parameter and captured-cell signatures.

Machine state includes call frames with PC/memory/operand base, shared
operand stack, typed heap references, constants, output, instruction count and
halt/error status. Entry is one parameterless unit function. Return validates the result
and abandons remaining operands in its own frame; a unit return uses an internal sentinel. Nested calls cannot pop caller
operands. Heap values include arrays/maps/records, enum payloads, function environments and
readonly capture cells; json is immutable and tagged.

Instructions: PUSH, LOAD, STORE, BIND, CAPTURE_VALUE, ACCESS; checked arithmetic/comparison/logical operations;
DUP/DUP2/POP/PRINT; CALL/FUNCTION/CLOSURE/INVOKE/RETURN; STACK_DEPTH/UNWIND; JUMP/JUMP_IF_FALSE/JUMP_IF_TRUE/TRAP;
ARRAY/MAP/RECORD, ENUM/ENUM_IS/ENUM_GET, INDEX/INDEX_SET, FIELD/FIELD_SET; VALUE_SET/VALUE_CHECK/VALUE_COLLECTION; BUILTIN;
ITER_SNAPSHOT with explicit single/pair mode; RESULT_BUILTIN/RESULT_DECODE and PROPAGATE.
The VM operand validation and dynamic behavior live in `pixellang/vm.py`;
native instruction emission lives in `selfhost/ir_expr.pxl` and its IR modules.

Calls to statically resolved declarations without captures use `CALL`, evaluating
arguments from left to right. Dynamic callees are evaluated before their arguments
and use `INVOKE`. Both paths check parameter types, access modes, call-depth limits
and the target's effect contract. Function types are `fn(T)->(R)` or
`effect_fn(T)->(R)`, determined by `effectFunctions`.

Closures capture readonly values through `CAPTURE_VALUE`. Later reassignment of an
outer binding does not change the captured value. Immutable payload storage can be
shared; captures cannot be used to mutate the outer binding. Explicit `inout`
parameters provide temporary write access, described below.

Validation rejects unknown versions/opcodes, malformed schemas/constants, invalid
slot/constant/jump targets, unknown calls and invalid entry signatures. Dynamic
checks enforce operand types, uninitialized memory, frame bounds, integer overflow
and resource budgets. Arbitrary external bytecode is not statically proven safe;
invalid execution raises a structured diagnostic rather than host code execution.

GC marks from frames, operand stack and transient allocation roots, then sweeps.
Deterministic identities and complete checkpoints support replay across GC. See
[RUNTIME_SPEC.md](runtime.md) for quotas and error/replay semantics.

## Native space and dataflow

Executables also carry `coordinateFrames` (nominal frames mapped to length units),
`effectFunctions` (effect-permitting function identities) and `flows` (typed graph
and state metadata). The self-hosted compiler emits these fields and the VM validates
them before execution; the renderer does not interpret the program.

| Instruction | Meaning |
| --- | --- |
| `SPATIAL_VALUE` | Construct a framed Point3 or Vec3 from x/y/z |
| `SPATIAL_BINARY` | Checked position/displacement arithmetic and comparisons |
| `SPATIAL_TRANSFORM` | Construct an explicit transform, checking rotation and target-frame translation |
| `SPACE_NEW` | Create an immutable space snapshot with a unique runtime identity |
| `SPACE_OP` | Execute space, entity, typed-link and transform operations |
| `FLOW_NEW` | Create an independent state instance of a declaration |
| `FLOW_STEP` | Advance an instance with typed inputs and commit atomically on success |
| `FLOW_CONFIG` | Read readonly constructor configuration for the current instance path; operand is the constructor parameter index |
| `FLOW_READ` | Read a cell from the current step's old state |
| `FLOW_STAGE` | Stage the next cell value until the step commits |

Checked graphs lower to functions, calls and slot operations in topological order.
The VM cross-checks `flows` metadata against executable instructions, preserving
named expressions, state, source ranges and persistent child relationships. It does not
reinterpret graph metadata as executable wiring at runtime. Stateful flows require explicit instances.
A failed step discards staged state, including nested instances. Runtime protection
also enforces readonly inputs and pure flow execution.

Point3, Vec3, EntityId, LinkId, Entity, Space and Link are native runtime values. Snapshot boundaries
isolate entity/link payloads. Extracted containers are values; later updates cannot
change a published snapshot. Complete
checkpoints include space identities, allocation budgets and flow state. Coordinate
source provenance is diagnostic metadata and does not affect equality, hashing or
numeric computation. See [native space](../../internals/spatial-model.md) and
[native dataflow](../../internals/dataflow.md) for the full contracts.

## Typed maps

The runtime accepts canonical `map<K,V>` types with invariant key and value types.
Keys use bounded structural identity for scalar, enum, Point3, Vec3, EntityId and LinkId values;
Containers and executable values cannot be keys. Iteration preserves
insertion order. Replacing a value preserves its position; deleting and reinserting
a key places it last. Duplicate constructor keys raise `collection.duplicate_key`.

Debug heap snapshots expose ordered `{key, value}` entries. JSON uses an object
for string keys and an array of those entries for other key types. Spatial and
flow snapshots freeze both keys and values; durable checkpoints encode the logical
values, never heap addresses or cached hash tokens. Recovery validates key types
and duplicates, then reconstructs lookup identities during recovery. Keys participate
in garbage collection, snapshot copying and spatial allocation accounting.

Native source and program PNG round-trips now cover `map[K, V]`. Compiler,
standard-library and example sources have been migrated, and the strict source
frontend rejects the single-argument form. MAP instructions use two-argument types; the old Python compilation frontend has been removed.

## Instance constructor inputs

`flows[id].initialInputs` describes creation-only parameters with name, node, slot
and type fields. These have separate slots and name scopes from per-step inputs;
source node identities must not collide. Constructor parameters are unavailable during steps.
`FLOW_NEW id` takes arguments from the operand stack according to this signature,
checks their types and freezes caller data. Each state initializer receives its
flow's complete constructor signature and returns that state's declared type.

A child flow node's `initializers` lists argument initializer functions in the
child constructor's parameter order. Each receives the parent constructor's inputs
and returns one child argument. Child state initialization follows argument
evaluation. Parameterless declarations omit these lists or use empty lists. A graph
with constructor parameters, and its ancestors, requires an explicit instance even
without directly declared state. PixelVM executes the functions; no host expression
interpreter or generated source participates.

Constructor data follows immutable-state payload restrictions. Every initializer
receives an isolated, read-only view; subsequent caller mutations cannot change
initial state. Initializers cannot perform I/O, call arbitrary functions or advance
other instances. Child argument initializers also cannot allocate spatial identity.
Space creation belongs to a particular state initializer and its instance path.

The runtime publishes a handle only after the entire instance tree initializes.
Failure or cancellation discards unpublished state and restores spatial identities
and the native allocation budget. Complete debugger checkpoints preserve jobs,
constructor values and the execution cursor for deterministic mid-initialization
replay. Temporary argument snapshots are released after creation. Durable application
checkpoints continue to contain committed state only.

The self-hosted compiler emits these descriptors from separate constructor parameter
lists, with native checking, IR, PNG round trips, source recovery and graph projection.
See the [constructor guide](../../guides/state-machines.md#constructor-parameters-and-step-inputs).
Runtime and source coverage live in `tests/test_flow_initial_arguments.py` and
`tests/test_native_flow_constructors.py` respectively.

## Values and checked conversions

Ordinary data has value semantics. A writable binding permits root replacement;
path writes construct an updated value without changing existing snapshots.
`FREEZE`, `THAW` and mutable-qualified data types are rejected. Checked decoding
and decompression return native Result values through `RESULT_DECODE` and
`RESULT_BUILTIN`; raw `JSON_DECODE` and `ZLIB_DECOMPRESS` are rejected.

## Value-path writes

`VALUE_SET` implements value-semantic assignment. The bundled development compiler
uses it for ordinary var writes. Its operand is a path of 1..128 selectors:
`{"field": "name"}` for record fields or `{"index": true}` for dynamic indices.
Stack order is the base value, evaluated dynamic indices in order, then the replacement.
The instruction consumes them and pushes the updated root. It never writes old objects:
only ancestor records, arrays and maps are copied, sharing untouched immutable subtrees.
A final map index may insert a key; intermediate keys must exist and array indices must
be in bounds. Type/path errors, cancellation and allocation failure leave old values
unchanged and release temporary GC roots. The compiler checks source lvalue permissions and preserves address evaluation
order. Exclusive access uses ACCESS, described below. VALUE_CHECK consumes the same path
operands and pushes nothing, checking a final write location before RHS evaluation.
Intermediate locations are read in order, so failure prevents later index evaluation.
These opcodes do not authorize writes to let bindings.

`VALUE_COLLECTION` takes `method` and `result` operands. Supported methods are array
append, extend and pop, plus map remove. Stack inputs are the receiver followed by
method arguments. Outputs are the updated receiver followed by the method result.
Append/extend return unit; pop/remove return Option of the element type, with None
for empty arrays or missing keys. The receiver remains unchanged; generated code
writes its root only after the full result has been produced. Map get uses the
existing readonly lookup operation and returns Option without writeback. Nested mutations obey the root-binding exclusive-access rules described below.

`CAPTURE_VALUE` is the runtime foundation for readonly value captures in the language
redesign. Its operand is a local slot or `{slot, type}`. It reads the current binding
value and pushes a fresh readonly capture cell without boxing or replacing the outer
binding. Immutable payload storage may be shared; explicit mutable values are rejected.
The cell cannot be written by STORE, and checkpoints preserve that restriction.
The bundled development compiler emits readonly captures; native source and editor
acceptance cover independence from later outer-binding writes.

### inout access (in development)

Function parameters retain access modes as `inout<T>`. These cannot be fields, elements,
captures or results. Function values may include such parameters, for example
`fn(inout<int>)->(unit)`; access modes must match exactly.

`ACCESS` takes `{slot, type, path}`: the current frame's root binding slot, the location's
declared value type, and an optionally empty path using VALUE_SET selectors. Dynamic
indices are pushed in path order and consumed by ACCESS. After validating the address,
it pushes a temporary access token. CALL/INVOKE pass it only to a matching inout parameter
and claim its lifetime on entry. LOAD reads ordinary values; STORE writes back through
persistent value updates. Tokens cannot be stored as ordinary values.

Access currently excludes other writes to the entire root binding. Nested borrows suspend
parent access; later ordinary arguments may read a pending borrow before call entry.
Each write publishes only after validation and allocation, preserving the old root on
failure. Earlier completed writes in an ordinary function do not roll back automatically;
flow whole-step atomicity is a separate contract. Return, errors and cancellation release
accesses. Checkpoints preserve scopes and root-binding relationships. Native source and
PNG acceptance are tracked in the [design principles](../../internals/design.md).

### Unified function effects

New compiler output includes `effectFunctions: [function identity]`. Listed functions
permit external effects; every other function has no external effects. Explicit inout writes remain observable. Executable function types use
`fn(...)->(...)` and `effect_fn(...)->(...)`; the latter displays as `effect fn(...)`
in source. Pure functions satisfy effect-permitting callback contracts, not vice versa.

Call boundaries enforce declarations even for empty implementations. A pure caller
cannot invoke an effectful callee, and an effectful caller cannot grant its permissions
to a pure callee. I/O and runtime identity operations from pure functions are execution
faults. `inout` controls parameter access independently and grants no external effects.
Flow nodes and initializers retain pure contracts.

`effectFunctions` is mandatory. Legacy `componentFunctions` metadata, including an
empty list, and `component(...)` function types are rejected. No legacy component
isolation or capture-copying execution branch remains. Source compilation, PNG
roundtrips, and recovered-source recompilation are verified; release evidence is governed by [verification](../../contributing/verification.md).

## Expression-flow bytecode

The bundled development compiler emits ordinary expression instructions for flows.
PNG recovery must reconstruct the same flow boundaries.
`flows[id].values` records named computations with expression nodes, slots and types. `children` records persistent child declarations,
target functions and constructor argument initializers. `dependencies` retains declaration/reference identities; `order` schedules named
computations, `updates` identifies state-update expressions, and `output` identifies the result node and slot.
Old `nodes`/`connections` wiring metadata is rejected.

`FLOW_CHILD_STEP {node, reference}` uses the owning flow's child declaration ID and the receiver's source reference.
Its function is resolved from validated children metadata. Appending the declaration ID to the parent's runtime path gives stable identity,
independent of call-expression positions. Ordinary CALL cannot invoke stateful flows.
Each child advances at most once per activation; mutually exclusive branches may contain different call sites. Skipped children retain state.
All participating updates publish together after the parent succeeds; checkpoints preserve participant paths and staged updates.
State cells absent from `updates` retain their committed values. Every declared update must still
execute exactly once; validation matches FLOW_STAGE to the declared update subset. No synthetic
self-assignment is needed. The bundled development compiler has passed focused source acceptance for this behavior.

Validation checks declarations, slots, state reads/stages, named-value stores, child targets, initializer permissions and return provenance.
Metadata supports type/debug provenance checks and is never reinterpreted as an executable wiring program.
Native execution, bytecode boundaries, program PNG recovery and Studio graph editing
are covered by development acceptance. Release requirements are defined in [verification](../../contributing/verification.md).

## Explicit result propagation

The development runtime validates native `Result<T,E>` schemas as
`Ok {value:T}` / `Err {error:E}`, and native `Option<T>` schemas as
`Some {value:T}` / `None {}`. Arbitrary enums cannot opt into propagation
by declaring similarly named variants.

`PROPAGATE {source, target}` examines the top operand once. Success replaces
that operand with its payload. Failure returns an Err or None in the current
function's result type, discarding only that frame's pending operands and using
normal return cleanup. Both types must use the same native family; Result error
types must match exactly. Success payload types may differ between source and
target. The target must equal the enclosing function's declared result.

Propagation cannot return from an enclosing closure or directly from a flow.
It releases pending inout access, retains writes already made through inout,
and does not throw a recoverable exception or implicitly roll back business
state. Source, PNG and runtime execution share this propagation contract.

## Checked data operations

`RESULT_BUILTIN {name, result}` calls a checked data/I/O primitive and returns the
native `Result<Success,Error>` named by `result`. The operation name must belong
to the checked-operation table, its arity must match, and the success type must
match that operation. `RESULT_DECODE {result}` consumes one JSON text string;
its success type specifies the concrete decoding target. Both instructions
require the exact native Result variant schema.

Arguments remain rooted until the operation and result allocation finish. Expected
data failures become Err; cancellation, resource limits, invalid operands and effect
violations remain execution failures. Result allocation itself is outside the data-error
boundary. These instructions do not roll back prior I/O or inout writes.

The bundled development compiler emits these instructions. Legacy `TRY`, `END_TRY`,
`JSON_DECODE`, `ZLIB_DECOMPRESS` and unchecked calls to checked primitives are rejected.

## Operand cleanup for expression exits

`STACK_DEPTH` pushes the current operand count relative to the active frame's base.
The compiler stores this marker in a temporary local on loop entry. `UNWIND slot`
reads that local and removes operands above the saved depth before a loop exit.
The marker must be an integer between zero and the current frame's operand count;
the instruction cannot remove caller operands. Markers use ordinary locals and
therefore participate in checkpoints without a separate hidden stack.

Only pending inout argument accesses in the discarded suffix are released. Retained
arguments and active parameters stay valid. Duplicating an access across the cleanup
boundary is rejected. `RETURN` discards the current frame's pending operands and
releases its accesses, preserving completed writes. These operations do not roll back
state. Local loops inside value blocks use the same cleanup while preserving outer
expression operands. Failed `?` propagation performs frame cleanup. Value blocks
cannot return to the enclosing function or break/continue an enclosing loop.

## Further reading

- [Documentation index](../../README.md)
