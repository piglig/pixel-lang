# Performance measurement

[中文](../../zh-CN/contributing/performance.md) · [English](performance.md) · [Documentation](../README.md)

Measure editor latency, individual compilation, PNG export/recovery and complete
verification separately. Small workloads do not represent the whole compiler. Fix
sources, compiler, runtime, inputs, options and power conditions; run timed workloads
sequentially without competing for CPU. Use fresh external output directories;
historical measurements are not retained in the documentation.

## Commands

```sh
.venv/bin/python scripts/benchmark_service.py --output /tmp/pixel-service-NEW.json
.venv/bin/python scripts/benchmark_studio_bundle.py --output /tmp/pixel-bundle-NEW.json
.venv/bin/python scripts/benchmark_incremental_ir.py --trials 3 --output /tmp/pixel-ir-cache-NEW
.venv/bin/python scripts/profile_selfhost.py --functions 100 --output /tmp/pixel-profile-NEW
.venv/bin/python scripts/profile_image.py --compiler pixellang/artifacts/compiler.json --self --timeout 1800 --output /tmp/pixel-image-full-NEW
```

Record wall time, VM instructions, peak memory, cold/warm state and artifact digests.
Profiling identifies hotspots; speedup comparisons run without probes. Add --plain
to the full PNG command to disable probes. It records power, input/recovered compiler
digests and RSS; the recovered compiler must execute a fresh program.

## Caching and parallelism

Studio measurements alternate separate requests and a shared transaction and compare
all artifacts. Exact artifact and parse caches are distinct layers. IR measurements
alternate reuseIR, set reuseArtifacts: false in both variants and warm parsing
identically. Record both initial-build and post-edit costs, compare digests and actual
execution. Hit counts do not prove speedup; population and transport can add cost.

Verification uses --timings-from to balance work without skipping tests;
--resume-from separately reuses identical successful stages. Compare 1/2/4 processes
on the same collection and report memory. Performance gates run alone after regression
workers exit. See [verification](verification.md). Remeasure after source or machine
changes; a measured ratio is not a performance guarantee. See the [native VM prototype](../internals/native-vm.md)
for its commands and scope.
