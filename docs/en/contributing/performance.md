# Performance measurement

[简体中文](../../zh-CN/contributing/performance.md) · [English](performance.md) · [Documentation](../README.md)

Freeze the workload before comparing cold starts, warm requests and edited builds. Report time, instructions and memory separately; verification duration is not editor latency.


**On this page**

- [Contract and interface](#contract-and-interface)
- [Commands](#commands)
- [Caching and parallelism](#caching-and-parallelism)
- [Native application recovery](#native-application-recovery)
- [Runtime profiling without rebuilding the compiler](#runtime-profiling-without-rebuilding-the-compiler)
- [Ordinary value updates](#ordinary-value-updates)
- [Single builds and session caches](#single-builds-and-session-caches)
- [Native compiler hot paths](#native-compiler-hot-paths)
- [Resident function-check and IR caches](#resident-function-check-and-ir-caches)
- [Further reading](#further-reading)

## Contract and interface

Measure editor latency, individual compilation, PNG export/recovery and complete
verification separately. Small workloads do not represent the whole compiler. Fix
sources, compiler, runtime, inputs, options and power conditions; run timed workloads
sequentially without competing for CPU. Use fresh external output directories;
historical measurements are not retained in the documentation.
Keep the system awake: connecting power alone does not prevent idle sleep. On macOS,
prefix the command with `caffeinate -i` to prevent idle sleep until that command exits.

## Commands

```sh
.venv/bin/python scripts/benchmark_service.py --output /tmp/pixel-service-NEW.json
.venv/bin/python scripts/benchmark_studio.py --project examples/ledger --output /tmp/pixel-studio-NEW.json
.venv/bin/python scripts/benchmark_studio_bundle.py --output /tmp/pixel-bundle-NEW.json
.venv/bin/python scripts/benchmark_incremental_ir.py --native-compiler pixellang/artifacts/compiler-native --trials 3 --output /tmp/pixel-ir-cache-NEW
.venv/bin/python scripts/profile_selfhost.py --functions 100 --output /tmp/pixel-profile-NEW
.venv/bin/python scripts/profile_image.py --compiler pixellang/artifacts/compiler.json --self --timeout 1800 --output /tmp/pixel-image-full-NEW
.venv/bin/python scripts/benchmark_png_pipeline.py --output /tmp/pixel-png-pipeline-NEW
```

`benchmark_studio.py` separates cold/warm interactive builds, on-demand execution-space
projection, PNG generation and recording restore. It asserts that ordinary builds and
restore omit pixel artifacts, exact-input reuse executes zero compiler instructions,
and viewing artifacts does not alter the recording. Its project timings have no
hardcoded latency target; the existing small-service limits remain a separate gate.
These timings cover the workstation service, excluding VS Code rendering and frontend
collection/transport. An unchanged build must make no compiler request and share only
the immutable code image; each build must create an independent VM.

Pass `--edited-project /path/to/edited-copy` to measure an actual source change after
the initial cold/warm pair. The second directory must contain the same entry and
differ from the first snapshot. The report records changed paths and both source
digests, and requires the edited build to miss the exact-artifact cache before its
repeat hits it. Use `--interactive-only` to omit projection, PNG and replay when
investigating editor latency; a successful run in this mode does not certify those
delivery paths. Keep cold, edited and unchanged timings separate: cold builds also
include compiler-worker startup.

The PixelLang output channel logs each completed Studio request's elapsed time and
time in the extension's transport queue. The remaining time includes service-side
waiting and execution; it is not a per-compiler-phase measurement. The uncached
`benchmark_native_compile.py` report records `serviceStartupSeconds` separately
from compilation samples so image loading is not confused with compilation work.

Record wall time, VM instructions, peak memory, cold/warm state and artifact digests.
Profiling identifies hotspots; speedup comparisons run without probes. Add --plain
to the full PNG command to disable probes. It records power, input/recovered compiler
digests and RSS; the recovered compiler must execute a fresh program.

`benchmark_png_pipeline.py` measures whole-compiler PNG export, compilation from PNG,
and source restoration sequentially in one compiler service, with parse, IR and
artifact caches disabled. It checks that the recovered compiler compiles a fresh
program. Use `--functions 10` to smoke-test the measurement tool, not as a substitute
for full-scale acceptance. Pass the same frozen compiler source map with
`--workload sources.json` to both code snapshots, compare each output digest,
and keep both output directories. Use `--compiler compiler.json --native-compiler compiler-native` to verify a candidate
without replacing the bundled artifact. The output retains `program.png`, the recovered
`compiler.json`, and source in `restored.json` for subsequent checks without decoding again.
Per-phase RSS is the cumulative process peak,
not an independent phase peak; the final peak also includes result verification.
`wallSeconds` uses the monotonic performance clock, which excludes suspend on some systems.
`clockElapsedSeconds` also records civil-clock elapsed time, and `clockGapSeconds` records
the difference. Suspend and clock adjustments can affect that gap; it is not CPU time.
Heap object counts and container sizes help locate allocations but do not replace
RSS: temporary copies, output objects and Python's allocator also consume memory.

PNG pipeline RSS measures only the host Python process and excludes the native compiler child;
it is a cumulative process peak, not total pipeline memory or an independent phase peak.
The PNG pipeline times all three operations with the native compiler and validates its
bytecode/executable/source-map binding. Small-program execution checks use the reference VM;
compiler smoke checks run outside the measured phases.

## Caching and parallelism

Studio measurements alternate separate requests and a shared transaction and compare
all artifacts. Exact artifact and parse caches are distinct layers. IR measurements
alternate incremental, set reuseArtifacts: false in both variants and issue identical warmup requests
identically. Record both initial-build and post-edit costs, compare digests and actual
execution. Hit counts do not prove speedup; population and transport can add cost.

Verification uses --timings-from to balance work without skipping tests;
--resume-from separately reuses identical successful stages. Compare 1/2/4 processes
on the same collection and report memory. Performance gates run alone after regression
workers exit. See [verification](verification.md). Remeasure after source or machine
changes; a measured ratio is not a performance guarantee. Label native compiler and Python VM measurements separately; timings from different execution paths are not interchangeable.

PNG compilation uses the bundled native compiler service. It does not build a separate
image worker or maintain a second disk parse cache. Use `benchmark_png_pipeline.py`
for end-to-end measurements and native self-host/source-recovery gates for complete
compiler delivery. Bounded transport checks remain in `test_image_transport.py`.

## Native application recovery

```sh
.venv/bin/python scripts/benchmark_native_recovery.py --output /tmp/pixel-recovery-NEW --events 1000 --samples 3
```

The native order example is the default workload; `--source` selects another entry.
The benchmark creates full-replay and persistent-checkpoint journals. Each sample uses
a fresh process and a copy of the same original journal, verifying both restored state
and the next event output. Reports include recovery time, total process time, journal
size, source fingerprint and power settings before/after measurement. Compilation and
journal construction are excluded. Run after other verification processes exit; the
output directory must be new and outside the repository.

## Runtime profiling without rebuilding the compiler

```sh
.venv/bin/python scripts/benchmark_native_compile.py examples/native-orders --repeat 5 --profile --output /tmp/pixel-orders-profile
```

Uses the bundled native compiler with incremental caching disabled to repeatedly
compile sources in the selected directory. It does not self-compile or emit PNGs.
Each sample creates and closes its own service, keeping first-request initialization consistent.
`serviceStartupSeconds` measures host service construction; native process startup and
first protocol initialization remain part of the request timing.
The output directory must not exist. `results.json` records timings, instruction
counts, bytecode digests, the compiler manifest and host source digests;
`sources.json` preserves the input snapshot. `--profile` adds a separate cProfile
run saved as `runtime.prof`, excluded from the timing median. Every run must
produce identical bytecode and instruction counts.
Timing excludes service startup, PNG processing and program execution. Power
conditions are not controlled. A local profiler improvement does not establish
an end-to-end self-compilation or delivery speedup.

`profile_selfhost.py` runs compiler bytecode in the instrumented Python VM, without
rebuilding a driver or invoking the Python compiler frontend. Use `--compiler` to
select frozen bytecode; the default loads and validates the bundled artifacts.
`phases.json` records exclusive stage timings and instruction counts at VM call
boundaries; helpers inherit the nearest mapped caller. `profile_image.py` also runs
in the reference VM: `--plain` disables instrumentation, not VM execution.
These probes locate stage work; their timings do not measure native compilation or Studio latency.

The old `benchmark_selfhost.py` has been removed. Synthetic function workloads use
the native compilation benchmark:

```sh
.venv/bin/python scripts/benchmark_native_compile.py --functions 100 --repeat 3 --output /tmp/pixel-functions-100-NEW
.venv/bin/python scripts/benchmark_native_compile.py --functions 200 --repeat 3 --output /tmp/pixel-functions-200-NEW
```

All functions are reachable and all outputs are checked after timing. Compare instruction
counts and timings without carrying forward the old VM's hard-coded thresholds.
This entry point also accepts paired `--compiler` / `--native-compiler` arguments,
records artifact digests and power state, and closes the service on failure.
Codec tests retain PNG correctness coverage; use the PNG pipeline above for delivery timings.

## Ordinary value updates

`python scripts/benchmark_value_updates.py --output /tmp/value-updates.json` measures array
append, indexed writes, map insertion and spatial flow state updates separately. Each source is compiled once; runtime
timing excludes compilation and VM initialization. Spatial steps insert entities while retaining
the first snapshot. The benchmark verifies outputs and array/spatial snapshot isolation, recording source/compiler/runtime fingerprints, power state, timings by size
and VM heap allocation counts. These counts exclude native spatial storage and are not total
process memory measurements. Use `--compiler` and `--native-compiler` for matching compiler bytecode and a native
executable; their mapping and digests are checked at startup. Use `--profile` for profiling.

Release comparisons require AC power, an idle machine and identical power settings; do not
measure alongside self-compilation. Profiling changes timings and diagnoses repeated work
rather than establishing final performance numbers. Value updates reuse validated entry types
and map keys, checking only changed data. Array updates share a radix-32 storage tree, copying only the changed path. Maps use a hash
prefix tree for lookup and a persistent array for insertion order, compacting deleted order
slots when they accumulate. Scalar maps need no heap-reference tracing; tracing costs for
reference-bearing containers still need further optimization. Alias isolation, history, allocation failure and
cancellation semantics must remain intact.

## Single builds and session caches

Use `incremental=False` for single-use driver builds to avoid retaining an unused session snapshot.
The content-addressed test-driver artifact cache is a separate layer. Development candidates
retain syntax, function checks and IR in native memory without semantic/IR file serialization.
Always report compiler bytecode and executable fingerprints. Disable reuseArtifacts and alternate incremental with identical inputs,
checking bytecode equality and reporting cold and edited builds separately.

The spatial-state benchmark advances repeatedly within one program execution, retaining
the existing spatial allocation budget. `completed: false` and `budget-exceeded` identify
a resource limit; `medianSeconds` is null. `verified` validates the measurement and input
checks, not completion at every size. Applications recount live spatial state at event
boundaries, so this single-execution limit is not an application entity-count limit.

## Native compiler hot paths

The native runtime sweeps access records only when a returning frame actually removes a
borrow. It propagates invalidation from ancestors to descendants, then frees descendants
before ancestors, avoiding repeated table scans while preserving exclusive-access and
lifetime checks. The compiler service uses `--buffered-output` to batch stdout writes and
checks the final flush before reporting success. Ordinary native programs still flush each
print by default; buffering does not change output budgets, cancellation or deadlines.

For single-file edits, profile parse-cache staging, whole-project semantic analysis,
bytecode generation, JSON transport and debug-image construction separately. A high parse
cache hit rate does not establish semantic incrementality. Parallel parsing cannot remove
whole-project work after those cache hits. Reduce repeated work through dependencies
before evaluating parallel execution.

## Resident function-check and IR caches

`incremental` controls native session reuse. Binding and declaration types have
separate declaration-query counters. `semanticCacheHits` and `semanticCheckedFunctions`
count top-level function and flow queries; closures belong to their enclosing query.
Flow IR, initializers and graph metadata are reused together. Frame planning also
has native queries, but its counters are not currently exposed by the service.

These counters do not cover all work: global indexes, constraint analysis and
specialization still need further incrementalization. `residentIRNodes` counts
structures, not bytes. See the [compiler service reference](../reference/compiler-service.md)
for query dependencies, invalidation, failure handling and capacity rules.

Compare matching candidate artifacts with:

```sh
.venv/bin/python scripts/benchmark_semantic_incremental.py \
  --compiler /path/to/compiler.json --native-compiler /path/to/compiler-native \
  --output /tmp/semantic-benchmark.json
```

The probe edits one compiler function and requires identical bytecode with and
without function caching. Report hits, recomputed functions and end-to-end time.
A high hit rate does not justify enabling a cache when serialization, restoration
and dependency validation increase total latency.

## Further reading

- [Documentation index](../README.md)
