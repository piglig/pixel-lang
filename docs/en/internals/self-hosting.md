# Self-hosting and source recovery

[简体中文](../../zh-CN/internals/self-hosting.md) · [English](self-hosting.md) · [Documentation](../README.md)

Self-hosting requires the compiler to compile itself and reach a complete-artifact fixed point on frozen source. PNG and text recovery verify separate delivery paths.

## Fixed-point procedure

Compile the current sources with a seed to prepare candidate bytecode and its matching native executable. Freeze the compiler sources, then use the candidate to produce successive generations. Compare complete canonical bytecode, not only example outputs, and record source and seed hashes to detect changed inputs.

The installed CLI and Studio reuse prepared artifacts; they do not bootstrap on each request. Native executables perform full compiler verification. Python coordinates transport and artifact checks; the reference VM supports small differential execution tests and debugging.

## Required verification

1. Define source, module, token, AST, binding, type and IR [interfaces](compiler-interfaces.md).
2. Lex the full language, comments and Unicode with precise invalid-input positions.
3. Parse declarations, precedence, control flow, generics, closures and imports, including all compiler sources.
4. Check all compiler sources and positive/negative scope, binding, inference, capture and control-flow cases.
5. Export the entire compiler through semantic PNGs, read, parse, check, lower and assemble it, then compile and run a fresh program. Images must not hide executable source, AST or bytecode.
6. Compare three complete generations from identical sources. Exercise generated compilers with language cases and multifile algorithms, strings, sorting and statistics; independent expectations include outputs and file effects.

A print-42 smoke is insufficient for real-project conformance. Native machine code
and WASM backends are not prerequisites for self-hosting.

## Commands and artifact identity

Use the repository .venv and a fresh external output directory for each run. The
candidate directory below must contain a compiler built from the current sources,
its matching native executable and map. An older seed is not a first-generation candidate:

```sh
.venv/bin/python scripts/verify_native_selfhost.py \
  --seed /tmp/pixel-candidate/compiler.json \
  --native-compiler /tmp/pixel-candidate/compiler-native \
  --output /tmp/pixel-native-NEW
.venv/bin/python scripts/verify_native_source_recovery.py \
  --compiler /tmp/pixel-native-NEW/compiler-g3.json \
  --native-compiler /tmp/pixel-native-NEW/compiler-g3 \
  --sources /tmp/pixel-native-NEW/sources.json \
  --image /tmp/pixel-native-NEW/compiler.png \
  --output /tmp/pixel-recovery-NEW
.venv/bin/python scripts/verify_generated_compiler.py \
  --compiler /tmp/pixel-recovery-NEW/compiler.json \
  --native-compiler /tmp/pixel-recovery-NEW/compiler-native \
  --sources /tmp/pixel-native-NEW/sources.json \
  --output /tmp/pixel-generated-NEW
```

Generated-artifact regressions build no seed and block the reference frontend. They
check the source snapshot against the current tree and record selections/omissions.
Bootstrap covers the two full-source checks. Generated-artifact regressions also run
lexer/parser tests over all compiler sources and examples, plus checker/IR/recovery/
service/cache, native editor and spatial PNG/recording tests. Compiler test drivers
are built by PixelLang. An exact source-hash match reuses the bundled artifact; other
drivers are cached by source, seed and runtime content, without rebuilding compiler
sources through the Python frontend. Recheck an existing full compiler PNG with
the native source-recovery command above, without exporting it again. The image,
source snapshot and paired compiler must belong to the version under acceptance.
Then run generated regressions on the recovered compiler. Isolated pixel-stage
checks remain in `test_selfhost_pixel_ast.py` and `test_selfhost_pixel_project.py`;
full workloads use the native PNG gate instead of a separate reference-VM compiler
pixel round trip.
See [verification](../contributing/verification.md) for the complete release workflow.

Record source/runtime hashes, resources, power, commands, errors and results.
Normalization may change JSON key order/whitespace only, never instructions,
constants, types, provenance or array order. Changed inputs require new validation;
interrupted or unfinished runs do not pass. Write results outside the repository;
historical run records are not retained as documentation.

## Further reading

- [Documentation index](../README.md)
- [Compiler architecture](architecture.md)
