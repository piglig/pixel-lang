# Layered verification

[简体中文](../../zh-CN/contributing/verification.md) · [English](verification.md) · [Documentation](../README.md)

Choose related, merge or release according to the change. Artifact acceptance also includes isolated installation and real-editor checks; these boundaries are not interchangeable.


**On this page**

- [Test organization](#test-organization)
- [Daily related changes](#daily-related-changes)
- [Before merge](#before-merge)
- [Before release](#before-release)
- [Performance budget](#performance-budget)
- [Resume completed stages](#resume-completed-stages)
- [Parallel scheduling](#parallel-scheduling)
- [GitHub CI and distributions](#github-ci-and-distributions)
- [Native spatial installation acceptance](#native-spatial-installation-acceptance)
- [Native compiler distribution](#native-compiler-distribution)
- [Further reading](#further-reading)

## Test organization

Standard-library effects, readonly access and value isolation live in `tests/test_stdlib_contracts.py`
and share one service initialization. Generated-compiler gates reuse that class with the candidate
artifact. `tests/test_collection_callbacks.py` retains an independent sorting oracle, PNG/source
recovery and replay boundaries instead of duplicating example delivery in another test file.

Name tests after current semantics or subsystems, not language versions, migration
batches or development phases. Basic syntax belongs in `tests/test_syntax.py`;
closures, generics, errors, collections and editor behavior have their own topics.
Cases with the same topic and lifecycle share a class-level compiler service and
close it during class cleanup. Distinct execution engines and delivery boundaries
still need separate verification; grouping topics does not remove their assertions.
When moving cases, update the generated-compiler runner and documentation references,
and compare test-method inventories and unique test IDs to catch missing cases or
duplicate collection through imported TestCase classes. Do not retain forwarding modules.

Parser syntax and rejection cases live in `test_selfhost_parser.py` and participate in generated-compiler acceptance.
`test_parser_backends.py` checks isolated/integrated AST equality, editor recovery and native/VM equivalence.
It reuses native drivers per class and does not use an older compiler as a migration oracle.

## Daily related changes

```sh
.venv/bin/python scripts/verify_preview.py related --output /tmp/pixel-related-NEW --test tests.test_compiler_service
```

At least one explicit test module/class/method is required. This is a targeted
check only. It does not silently run bootstrap or claim whole-project acceptance.

## Before merge

```sh
.venv/bin/python scripts/verify_preview.py merge --workers 2 --output /tmp/pixel-merge-NEW
```

Runs host language/project regression (all non-`test_selfhost*` modules), generated
compiler checker/IR/recovery and service/incremental-cache regression, real Node Bridge and headless DAP checks,
and the installed-service performance budget. The installed artifact and manifest
must match current compiler sources. Full-source compiler checking/lowering is
reserved for bootstrap, as recorded by the generated regression runner. Real
VS Code extension-host acceptance is separately required for releases.

## Before release

```sh
.venv/bin/python scripts/verify_preview.py release --workers 2 --output /tmp/pixel-release-NEW
```

Includes the merge gate, then all three compiler generations with complete
artifact equality, full uncached compiler PNG delivery/recovery, recovery using
the generated compiler, complete source recovery/self-compilation, and regressions
using the PNG-recovered compiler. Source recovery must rebuild the compiler, use
that compiler to compile the recovered sources again with identical bytecode, and
execute a smoke program. Recovering executable bytecode alone does not satisfy this gate. The
installed artifact must equal the accepted stage3 artifact. This command is
intentionally expensive; daily edits should use the related gate.

A passing release compiler gate does not substitute for package/VSIX installation,
real editor tests, or a release report tied to the same frozen snapshot.

## Performance budget

```sh
.venv/bin/python scripts/benchmark_service.py --output /tmp/pixel-performance-NEW.json --limits config/service-performance-limits.json
```

The fixed two-file workload measures cold startup/check, warm check, body edits,
compilation, PNG export/recovery and child peak RSS. It checks execution results
before and after recovery. Thresholds in `service-performance-limits.json` are
coarse regression ceilings based on the recorded development workload, allowing
headroom for process startup and machine noise. They are not UI latency promises
or limits for the full compiler PNG workload.

Both time and VM instruction limits are enforced. Memory is measured from reaped
child-process high-water RSS (macOS bytes/Linux KiB normalized to bytes). Fresh
output files preserve power snapshots and, on macOS, AC/battery low-power settings.
Final comparative performance reporting requires matching power conditions and
workloads; a passing small-workload gate alone does not prove that comparison.

Validation of gate behavior includes a passing real measurement and a deliberately
failing warm-check budget of one VM instruction, which returns exit code 1 and
records the violation. Tests also verify real test-failure propagation, refusal
to overwrite prior evidence, and rejection of nonfinite/incomplete thresholds.

## Resume completed stages

```sh
.venv/bin/python scripts/verify_preview.py merge --workers 2 \
  --resume-from /tmp/pixel-merge-PREVIOUS --output /tmp/pixel-merge-RESUMED
```

Always use a new output directory. Resume requires identical source/tool/test input
hashes, gate selection, worker count, Python/platform/dependency identity and
normalized command arguments. Only completed successful stages with every output
hash intact can be copied; changed inputs and corrupted/missing outputs fail closed.
Failed stages rerun. Source evidence is not rewritten; its embedded original paths
remain historical, and the new summary records `reusedFrom` and `originalSeconds`.

Performance stages always rerun. `fresh: false` identifies any reused stage. Omit
`--resume-from` for fresh complete release bootstrap and PNG verification; resumed
results must not be described as a new uncached full run. Reports without stage receipts cannot be reused. Exact reuse is deliberately conservative: changing a
runtime/compiler/test input invalidates the whole previous gate.

## Parallel scheduling

`--workers` applies to both related and merge regression stages. Each test class
stays contiguous in one process to avoid repeating expensive class setup. The worker
count is an upper bound; one test class uses one process. Independent classes are
balanced using historical test costs or case counts. Compiler generations retain their dependency order.
Performance stages run after regression processes exit.

```sh
.venv/bin/python scripts/verify_preview.py merge --workers 2 \
  --timings-from /tmp/pixel-merge-PREVIOUS --output /tmp/pixel-merge-NEW
```

Historical test durations balance shards only. They never skip tests or turn an old
success into a new acceptance result. Use the same timing history and test collection
when comparing worker counts; report wall time and memory together. The sum of worker
peak RSS is a conservative sum of individual high-water marks, not a measurement of
simultaneous total memory. `--resume-from` is a separate, explicitly recorded mechanism.

Full self-hosted lexer, parser and planning corpora use a native driver bound to the tested bytecode. A batch prepares its driver once; each execution keeps independent resource limits. Small valid and invalid cases retain native/VM differential checks, while the full corpus still checks lexer reference results or AST/planning constraints. Reusing a driver does not reuse test outcomes, and faster validation is not a measurement of Studio compilation latency.

## GitHub CI and distributions

PRs and main pushes run `.github/workflows/ci.yml`: Linux/Python 3.11 and macOS/Python 3.13 regression, bilingual documentation checks, extension checks, packaging, clean installation and real VS Code acceptance. `full-acceptance.yml` runs full bootstrap and PNG roundtrips separately, manually or weekly. Official GitHub Actions are pinned by commit; logs and evidence are retained as workflow artifacts.

Manually run `preview-release.yml` for all release checks and downloadable verified packages. Pushing the exact alpha tag declared in `config/release.json` runs the same checks, then publishes a GitHub prerelease only after every gate passes. Ordinary pushes and manual verification do not publish releases. Marketplace publication is separate.

```sh
.venv/bin/python scripts/build_distribution.py --output /tmp/pixel-distribution-NEW
.venv/bin/python tests/clean_install.py /tmp/pixel-distribution-NEW/*.whl --report /tmp/pixel-clean-NEW.json
```

`config/release.json` explicitly maps Python `0.9.0a1`, extension `0.9.0` and preview tag `v0.9.0-alpha.1`. Deliveries include a wheel, VSIX, package/compiler identity manifest `release.json` and `SHA256SUMS`. Packaging checks both archives for matching licenses, compiler and versions and fails if inputs change. Successful packaging alone does not prove full release acceptance.

## Native spatial installation acceptance

Native spatial acceptance also checks the installed runtime; imports from the
source checkout are not a substitute:

```sh
.venv/bin/python scripts/verify_native_install.py \
  --wheel /tmp/pixel-distribution-NEW/*.whl \
  --compiler pixellang/artifacts/compiler.json --report /tmp/pixel-native-install-NEW.json
```

The script creates an isolated environment, checks the installation path and
compiler hash, and runs neighborhood, weighted-route and multi-file native elevator programs. It emits PNGs,
deletes the original sources, executes the images, recovers source and changes a
recovered link weight to verify the new output. This does not replace full compiler
PNG delivery verification.

Install the VSIX into a separate isolated VS Code profile. The `python` argument
points to `env/bin/python` in the environment reported by the previous step:

```sh
node vscode/scripts/test-installed.js \
  /tmp/pixel-distribution-NEW/pixellang-studio-0.9.0.vsix \
  /path/to/isolated/env/bin/python pixellang/artifacts/compiler.json \
  /tmp/pixel-native-vsix-NEW.json
```

The script verifies the extension's isolated installation path and bundled compiler
identity, then runs native spatial extension-host acceptance. Coverage includes
link payload paging, history, unsaved coordinate changes, rejection of stale debug
views and neighborhood results after restart. Desktop pointer dragging and keyboard
undo still require separate real interaction acceptance.

Set `PIXELLANG_TEST_SUITE` to select a focused installed-extension suite. Use a
new report path for each run and run editor suites sequentially, since they share
the desktop. The VSIX and isolated Python environment remain the inputs above.

| Suite | Coverage |
| --- | --- |
| `native-spatial-debug` (default) | Spatial debugging, coordinate edits and restarted execution |
| `native-graphs` | Unsaved graph edits, expired actions, undo and projection refresh |
| `native-computed` | Computed positions, source navigation and protection against literal overwrite |
| `native-format` | Native formatting of unsaved text, missing imports, idempotence and stale responses |
| `application` | Typed events, recording/replay, event debugging, durable journals |

For example, prefix the installed-test command with
`PIXELLANG_TEST_SUITE=native-format` and choose a separate formatter report.
`native-spatial-pointer` waits for actual desktop dragging and keyboard undo;
starting it does not prove interaction success. Its terminal result must pass.

## Native compiler distribution

The default installed compiler requires a native executable, its source map,
compiler bytecode and a matching manifest. It fails explicitly if the native
bundle is absent, changed or built for another platform. Build on each target
platform with Python, a C11 compiler and zlib development headers available.
A checked-in bytecode seed can generate a native seed without launching the
installed compiler service:

```sh
.venv/bin/python - <<'PYTHON'
import json
from pathlib import Path
from pixellang.native_codegen import build_native
code = json.loads(Path("pixellang/artifacts/compiler.json").read_text())
build_native(code, "build/native-seed/compiler", optimization=1, max_depth=1024)
PYTHON
.venv/bin/python scripts/verify_native_selfhost.py \
  --seed pixellang/artifacts/compiler.json \
  --native-compiler build/native-seed/compiler \
  --output build/native-acceptance
.venv/bin/python scripts/stage_native_compiler.py \
  --acceptance build/native-acceptance --output build/native-bundle
```

Use new output directories for each acceptance run. The compiler uses
`optimization=1` with function inlining disabled. This compacts temporary stack
storage without the expensive code expansion of full optimization. Unoptimized
compiler builds can exhaust the native stack before reporting deep parser input;
therefore `optimization=0` is not the compiler acceptance configuration.
The acceptance command checks successive compiler generations, complete PNG
export/recovery, recovered compiler self-compilation and a smoke program.

Staging does not activate or publish anything. Back up `pixellang/artifacts`,
then copy the four files from the verified bundle into it (manifest last). Run
`scripts/build_distribution.py --output <new-directory>` to build and check the
platform wheel and VSIX. Complete isolated installation and Studio acceptance
before publishing. The macOS deployment target comes from the actual executable;
a local Linux wheel is not a manylinux portability claim.

The separate native text-recovery gate recovers the complete module closure of
the compiler entry from PNG, compiles the recovered text, builds that compiler
as machine code, and requires it to reproduce its own recovered sources exactly.
It must also compile the original source snapshot into the exact canonical candidate
bytecode (`canonicalCompilerEqual: true`); a self-consistent recovered compiler alone
is insufficient.
A source snapshot can also contain independent entry points; they are not part
of this PNG. The gate discovers the expected closure using the native parser,
checks all input fingerprints, and runs the recovered compiler smoke in both the
reference VM and native code, requiring the expected output from each. This is the
single full compiler source-recovery gate; the reference VM does not repeat the
entire compiler self-compilation workload. Use a fresh output directory and inspect `summary.json`; only
`verified: true` confirms success.

```sh
.venv/bin/python scripts/verify_native_source_recovery.py \
  --compiler build/native-acceptance/compiler-g3.json \
  --native-compiler build/native-acceptance/compiler-g3 \
  --image build/native-acceptance/compiler.png \
  --sources build/native-acceptance/sources.json \
  --output build/native-text-recovery
```

Generated-artifact regression accepts a matching native candidate for its compiler-service
tests. The release runner supplies this executable for both the installed and
PNG-recovered compiler. The runner validates the bytecode/executable/map binding
and records their hashes; it never replaces the candidate with an installed
compiler. Tests that inspect VM state or directly execute component drivers still
use the reference VM, so this option does not make the entire suite native.

```sh
.venv/bin/python scripts/verify_generated_compiler.py \
  --compiler build/native-acceptance/recovered-compiler.json \
  --native-compiler build/native-acceptance/recovered-compiler \
  --sources build/native-acceptance/sources.json \
  --output build/recovered-regression --workers 4
```

## Further reading

- [Documentation index](../README.md)
