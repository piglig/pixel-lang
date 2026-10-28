# State machines and event applications

[简体中文](../../zh-CN/guides/state-machines.md) · [English](state-machines.md) · [Documentation](../README.md)

Start with a flow that handles business events only. Add a logical clock explicitly when needed; recordings and journals are host execution tools.


**On this page**

- [Business events without a clock](#business-events-without-a-clock)
- [Register an application](#register-an-application)
- [Logical time and event order](#logical-time-and-event-order)
- [Run and inspect](#run-and-inspect)
- [Logical time library](#logical-time-library)
- [Recording and limits](#recording-and-limits)
- [Constructor parameters and step inputs](#constructor-parameters-and-step-inputs)
- [Further reading](#further-reading)

## Business events without a clock

```pixellang
enum Event { Add(amount: int), Reset }
flow Counter(initial: int) step(event: Event) -> int {
    state count = initial
    let updated = match event { .Add(amount) => count + amount, .Reset => initial }
    next count = updated
    output updated
}
effect fn main() { Counter(10) }
```

Constructor parameters are readonly configuration available during every step. A single-input application
processes business events in enqueue order, without a clock or a Tick variant. Each actual event causes one
transition; an empty queue cannot advance, and Studio stops playback when the queue is drained.
The first output comes from the first event, without a synthetic initialization event. Recording, seeking,
debugging and durable journal recovery remain available.

Single-input applications accept an `instance` selection but reject timer configuration and future scheduling.
A CLI event file can contain `[{"input":{"variant":"Add","fields":{"amount":5}}}]` without a tick.
Applications needing timer events explicitly use the two-input clock protocol below.

## Register an application

```pixellang
import "std/time.pxl" as time

enum Event { Tick, Add(value: int) }
fn Update(previous: int, event: Event) -> int {
    match event {
        Event.Tick => { return previous },
        Event.Add(value) => { return previous + value }
    }
}
flow Counter() step(event: Event, clock: time.Step) -> int {
    state total: int = 0
    let updated = Update(total, event)
    next total = updated
    output updated
}
effect fn main() { Counter() }
```

Create the flow in main, then let main finish normally. The host selects and schedules the instance. By default there must be exactly one root instance, with a Tick event and a 100 ms logical interval. CLI `--config application.json` or Studio `pixellang.application` settings can specify `instance`, `tick`, and `intervalNanoseconds`. These are host configuration, not language syntax. The application flow accepts an enum event followed by a clock record; the interval is positive integer nanoseconds.

The clock protocol requires `index: int`, `elapsed: Duration`, and `delta: Duration`; the duration record contains only `nanoseconds: int`. Clock records remain nominally typed in the language. The boundary validates their fields rather than optional module names, so executable PNG recovery preserves the contract even without original names.

## Logical time and event order

- The first transition is Tick 0, with elapsed time and delta both zero.
- Tick N occurs at `N × interval`, with delta equal to the interval for N > 0.
- Inputs queued for a tick run after its tick event, in enqueue order, with zero delta. Multiple inputs do not advance time.
- An input without a scheduled tick targets the current tick, or Tick 0 before startup.
- Each normally returning event commits once. Execution faults restore VM and flow state, retaining the pending input for retry or explicit discard. A business Result.Err is a normal return and is consumed and recorded; the transition function explicitly decides whether to retain business state.
- Seek reconstructs historical native state. Return to **Latest** before adding inputs or advancing the live run. Playing from history replays existing events first.

XYZ are spatial coordinates; time is an independent logical value. Display refresh rates and playback speed never change program inputs. Integer nanoseconds avoid accumulated clock drift; duration scaling and clock overflow are checked.

## Run and inspect

```sh
pixel application examples/native-orders/main.pxl --events examples/native-orders/events.json --steps 5 --record /tmp/orders.pxevents
pixel application examples/native-orders/main.pxl --replay /tmp/orders.pxevents --steps 0
```

`--steps` counts event transitions, including inputs. `--seek N` inspects the boundary after N recorded events; zero is the registered application before its first event. `--replay` restores a recording, returns to its latest event, then runs the requested number of additional steps. Use `--steps 0` for inspection only.

The entry can also be an executable PNG. Use the regular PNG export and recovery commands; the application has no separate hidden bytecode payload.

In VS Code, run **PixelLang: Open Native Application** and choose a `.pxl` entry or PNG. The panel provides playback, pause, step, history, typed event variants, state inspection, and native 3D output. Field text is decoded by the VM against the event enum; signed 64-bit integers remain exact across the editor boundary. Complex fields can use the displayed record/enum schemas. Pending inputs can be explicitly discarded.

**Debug event** reconstructs the selected completed event in an isolated VM and opens the existing source debugger, including stepping backward and native flow inspection. This does not alter the live application. Save the application recording to reproduce these debug sessions later.

## Logical time library

`std/time.pxl` exports `Duration`, `Step`, `Nanoseconds`, `Milliseconds`, `Seconds`, `InSeconds`, `At`. `At(0, interval)` has zero delta.

## Recording and limits

### Durable application journals

```sh
pixel application examples/native-orders/main.pxl --journal /tmp/orders.pxjournal --events examples/native-orders/events.json --steps 5
pixel application examples/native-orders/main.pxl --journal /tmp/orders.pxjournal --steps 10000
pixel application examples/native-orders/main.pxl --journal /tmp/orders.pxjournal --steps 0 --seek 3
```

`--journal` creates or resumes a SQLite transaction journal. Input acceptance, explicit discard and successful events return success after their transaction commits. Recovery preserves pending inputs and their order. Do not resubmit an already accepted `--events` file when resuming unless you intend to send those inputs again.

Recovery validates the complete journal digest chain, restores typed state from the nearest valid checkpoint, then executes subsequent commands while checking event ordering and output digests. Journals bind to the compiled program, interval, startup inputs and runtime budgets; they contain no executable VM snapshots. Process termination before commit cannot publish a partial command. An uncertain commit or a stale concurrent writer closes the session for writes; reopen the journal to recover its committed state.

Event descriptors are read from disk in pages of at most 128 events. `--seek` reconstructs a boundary in an isolated VM without changing the durable live frontier. Memory does not retain all past events. The default journal payload limit is 1 GB, excluding database index and transaction overhead. Journals have no 4,096-event limit.

In VS Code, run **PixelLang: Open Native Application Journal**, choose to create or resume a journal, then open the corresponding source or PNG. The panel supports playback, stepping, history inspection and typed inputs. **Debug event** reconstructs the selected event from the journal for source stepping and reverse debugging in an isolated VM. Committed operations are saved automatically; no in-memory recording export is needed. Closing the panel releases the session; reopening resumes the journal's latest committed state.

Every 128 successful events, the runtime attempts to save a state checkpoint in the same transaction. At most eight checkpoints are retained within a combined 64 MB payload budget. Checkpoints contain flow state, spatial and link identities, logical time, pending inputs and the latest output. Shared immutable values are encoded once. Temporary VM heaps and instruction positions are not saved; the original program registers the execution entry again during recovery. Oversized checkpoints are skipped without rejecting the event; recovery can replay from an earlier position.

Journal mode cannot be combined with `--history`, `--record` or `--replay`. History inspection additionally retains a process-local prefix cache of at most 64 MB, so forward inspection executes only additional commands. Initial inspection, backward seeks, event debugging and recovery after restart can use disk checkpoints. Integrity validation still reads the corresponding log prefix, so recovery includes log I/O and digest costs. The current journal format is version 2 and is incompatible with the earlier development format. Journal execution requires a fresh VM without external file capabilities; durable external business effects require a separate protocol.

### In-memory debug recordings

`.pxevents` records bind to the exact compiled program and interval. Restoration executes typed inputs and checks every output; it does not deserialize executable state. A modified or differently compiled program needs a new recording. The document also retains the selected history position and pending inputs.

The CLI executes without accumulating history by default, retaining only the latest output and current state. For example, `--steps 10000` is not subject to the recorder's 4,096-event limit. `--history` explicitly enables history; `--record`, `--replay`, and `--seek` also enable it. Unrecorded execution cannot recover past event history afterward. Both modes share event scheduling, clock semantics and atomic commits.

Execution defaults allow 1,024 pending inputs, a 64 MB pending-input budget, one million instructions per event, and a 64 MB per-result budget, in addition to VM heap and logical-time limits.

After each successful event, native allocation accounting is rebased to spatial values and flow state still reachable from the VM; shared snapshots count once. Discarded temporary values do not accumulate charges indefinitely. Temporary allocations within one event and growing live state remain bounded. Queues, outputs and history have separate host budgets.

Studio and CLI sessions with in-memory history additionally allow 4,096 completed events, a 64 MB history byte budget, and a separate 64 MB checkpoint budget. Older checkpoints may be evicted; deterministic replay retains access to history. Reaching a recording limit stops advancement with an error instead of silently losing records. Continuous execution and disk journals use the separate modes above; the runtime does not provide external business transactions or a real-time rendering engine.

See the [order example](../../../examples/native-orders/README.en.md), and [native spatial language reference](../reference/language/spatial.md).

## Constructor parameters and step inputs

In `flow Counter(start: int) step(delta: int) -> int`, the first list supplies creation-only
parameters and the second supplies per-step inputs. A flow without constructor
parameters still writes `()` before `step(...)`. Constructor parameters are readonly instance configuration, visible in
state initializers, child constructor arguments and per-step expressions. They are saved by value at creation;
later caller changes do not affect them, and closures capture them by value. Only changing values need state cells.

```pixellang
flow Counter(start: int) step(delta: int) -> int {
    state value: int = start
    let updated = Add(value, delta)
    next value = updated
    output updated
}
fn Add(previous: int, delta: int) -> int = previous + delta
effect fn main() {
    let first = Counter(10)
    let second = Counter(100)
    print(first.step(1))
    print(second.step(1))
}
```

The output is 11 and 101. Arguments are evaluated once, left to right, and instances
own independent initial state. In a parent flow, `instance child = Counter(start + 1)`
configures a child from the parent's constructor parameters. Child arguments cannot
depend on step inputs, other state cells or computation results.

Initializers allow records, enums, containers, spatial values, constructor-parameter
fields and indexing, and arithmetic. Arbitrary function calls, I/O and implicit
clocks are not allowed. Compute more complex values explicitly before constructing the instance.
Constant expressions still fold during checking; parameter-dependent calculations
run during creation, and failures publish no partial instance. Creation arguments
follow their declared types and state-payload restrictions. Ordinary records and
containers have value semantics; later local updates cannot change an existing
instance’s initial values.

The application host constrains only the step signature to an event and clock. Constructor
parameters are separate. The order example uses `Order(initial)` to supply
identity, amount and deadline; `pixel run examples/native-orders/instances.pxl`
exercises two independently configured orders.

### Editing creation parameters in Studio

The native graph panel's **Add child instance** form displays creation inputs from the selected
flow signature. Enter one PixelLang expression per field. Creation parameters are
shown separately from step inputs; advance the child explicitly with `child.step(...)`.
Select an existing child instance, edit **Creation parameters**, and click **Update creation parameters**
to write the expressions back to source. One editor undo restores the edit. Changes
affect subsequently created instances; restart execution to observe them in a running application.

The native compiler checks expression syntax, types, and initialization restrictions
before writing. Incomplete flow structure does not suppress constructor checking; other binding
or type errors may need repairing first. Edits use native AST ranges and preserve
comments and line endings outside the replaced expressions. If an existing argument
list has the wrong number of arguments, repair its count in the text editor first.

## Further reading

- [Documentation index](../README.md)
- [Install the toolchain](installation.md)
