# Native VM prototype

[中文](../../zh-CN/internals/native-vm.md) · [English](native-vm.md) · [Documentation](../README.md)

Status: opt-in experiment, not the installed runtime default. This prototype
compiles the existing VM implementation into a local extension using Cython. It
keeps CPython objects, checked language operations, the GIL and Python dependencies;
it is not a standalone C/Rust VM or a native machine-code backend for PixelLang.

The first experiment isolates the cost of interpreted VM dispatch while retaining
one semantic implementation. It deliberately avoids inferred C integer types or
unsafe arithmetic shortcuts. A separate unboxed VM remains a possible later design.

VM hot-path changes require rebuilding this extension before matched comparisons.

## Build and compare

From the repository root, with a local C compiler:

```sh
uv run --no-project --python .venv/bin/python --with Cython==3.1.8 --with setuptools==80.9.0 \
  python scripts/build_native_vm.py --output /tmp/pixel-native-NEW
.venv/bin/python scripts/native_vm_probe.py --output /tmp/pixel-python-NEW
.venv/bin/python scripts/native_vm_probe.py --native /tmp/pixel-native-NEW/manifest.json \
  --output /tmp/pixel-native-measurement-NEW
```

The build environment is isolated; Cython is not added to user runtime dependencies.
Generated C, annotations, binary and build identity stay outside the checkout. The
loader verifies VM source hash, extension hash and ABI before replacing the VM only
inside the probe process. Production service processes continue using Python VM.

Run the two measurements sequentially under matching power conditions. Both use
the same installed compiler, source project and resource limits. Compare all
`resultSha256` values and VM instruction counts before comparing time. The default
40-function workload compiles, exports PNG, recovers executable bytecode and checks
both programs print 42. `--self` instead uses all compiler sources and requires the
recovered compiler to compile and run a fresh program. `--profile` records cProfile
statistics; profiling overhead makes its wall time unsuitable for speedup claims.

## Semantic checks

```sh
.venv/bin/python scripts/native_vm_probe.py --native /tmp/pixel-native-NEW/manifest.json \
  --output /tmp/pixel-native-tests-NEW --test tests.test_compiler_service \
  --test tests.test_runtime --test tests.test_temporal --test tests.test_debugvalues \
  --test tests.test_selfhost_debug
```

This set covers service artifacts and cancellation, heap/GC aliases, checkpoints,
replay, variable inspection and Studio mappings. It is a scoped prototype check,
not complete release/platform acceptance. Python VM remains the reference. Native
packaging would additionally need platform wheels, installation tests and full
runtime/regression acceptance before becoming a default.

Build behavior follows [Cython compilation documentation](https://cython.readthedocs.io/en/stable/src/userguide/source_files_and_compilation.html).

For a full-compiler comparison without repeating expensive PNG recovery:

```sh
.venv/bin/python scripts/native_vm_probe.py --self --compile-only --output /tmp/pixel-self-python-NEW
.venv/bin/python scripts/native_vm_probe.py --self --compile-only \
  --native /tmp/pixel-native-NEW/manifest.json --output /tmp/pixel-self-native-NEW
```

`--compile-only` explicitly excludes PNG evidence. Its output contains the complete
compiled artifact for byte-for-byte comparison. Test subprocesses do not inherit
the prototype loader; native claims apply to in-process VM execution only.
