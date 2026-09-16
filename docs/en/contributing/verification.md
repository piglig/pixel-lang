# Layered verification

[中文](../../zh-CN/contributing/verification.md) · [English](verification.md) · [Documentation](../README.md)

Run commands from the repository root with `.venv/bin/python`. Run output goes to a
new directory outside the checkout, preventing generated
reports from changing the tested inputs. A gate returns nonzero for failed tests,
unverified child evidence, timeouts or changed input fingerprints.

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
the generated compiler, and regressions using the PNG-recovered compiler. The
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

`--workers` applies to both related and merge regression stages. Independent tests
run in bounded processes; compiler generations retain their dependency order.
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

## GitHub CI and distributions

PRs and main pushes run `.github/workflows/ci.yml`: Linux/Python 3.11 and macOS/Python 3.13 regression, bilingual documentation checks, extension checks, packaging, clean installation and real VS Code acceptance. `full-acceptance.yml` runs full bootstrap and PNG roundtrips separately, manually or weekly. Official GitHub Actions are pinned by commit; logs and evidence are retained as workflow artifacts.

Manually run `preview-release.yml` for all release checks and downloadable verified packages. Pushing the exact alpha tag declared in `config/release.json` runs the same checks, then publishes a GitHub prerelease only after every gate passes. Ordinary pushes and manual verification do not publish releases. Marketplace publication is separate.

```sh
.venv/bin/python scripts/build_distribution.py --output /tmp/pixel-distribution-NEW
.venv/bin/python tests/clean_install.py /tmp/pixel-distribution-NEW/pixellang-0.9.0a1-py3-none-any.whl --report /tmp/pixel-clean-NEW.json
```

`config/release.json` explicitly maps Python `0.9.0a1`, extension `0.9.0` and preview tag `v0.9.0-alpha.1`. Deliveries include a wheel, VSIX, package/compiler identity manifest `release.json` and `SHA256SUMS`. Packaging checks both archives for matching licenses, compiler and versions and fails if inputs change. Successful packaging alone does not prove full release acceptance.
