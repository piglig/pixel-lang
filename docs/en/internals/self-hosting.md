# Bootstrap and self-hosting verification

[中文](../../zh-CN/internals/self-hosting.md) · [English](self-hosting.md) · [Documentation](../README.md)

The Python reference compiler builds selfhost/*.pxl into a stage0 seed. The PixelLang
compiler then produces stage1, stage2 and stage3 from identical frozen sources.
Fixed-point checking compares complete normalized bytecode, not just sample output.
Installed CLI/Studio use a pinned artifact without repeating bootstrap per request.
Python supplies the VM, transport and general bounded file/byte/compression operations;
no hidden host frontend runs after the seed.

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

Use the repository .venv and a fresh external output directory for each run:

```sh
.venv/bin/python -m pixellang.bootstrap --output /tmp/pixel-bootstrap-NEW
.venv/bin/python -m pixellang.bootstrap_image --output /tmp/pixel-image-NEW
.venv/bin/python scripts/verify_generated_compiler.py --compiler /tmp/pixel-bootstrap-NEW/stage3.json --sources /tmp/pixel-bootstrap-NEW/sources.json --output /tmp/pixel-generated-NEW
```

Generated-artifact regressions build no seed and block the reference frontend. They
check the source snapshot against the current tree and record selections/omissions.
Bootstrap covers the two full-source checks; the runner selects the remaining
checker/IR/recovery/service/cache tests. Recheck an existing full compiler image
with verify_compiler_image.py using --compiler, --sources, --image and --output,
then run generated regressions on its recovered compiler.json.
See [verification](../contributing/verification.md) for the complete release workflow.

Record source/runtime hashes, resources, power, commands, errors and results.
Normalization may change JSON key order/whitespace only, never instructions,
constants, types, provenance or array order. Changed inputs require new validation;
interrupted or unfinished runs do not pass. Write results outside the repository;
historical run records are not retained as documentation.
