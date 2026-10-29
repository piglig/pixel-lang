# Contributing

[简体中文](../../zh-CN/contributing/README.md) · [English](README.md) · [Documentation](../README.md)

Bug reports, documentation improvements and code changes are welcome. Address a clear problem and provide reproducible inputs and verification results.

## Prepare the environment

Complete [source installation](../guides/installation.md), then install extension dependencies:

```sh
npm --prefix vscode ci
```

Start with [architecture](../internals/architecture.md), [compiler interfaces](../internals/compiler-interfaces.md) and [Python responsibilities](../internals/python.md).

## Report a problem

Include minimal source, commands, expected and actual behavior, operating system and Python version in a GitHub Issue. For compiler or image problems, include relevant `pixel doctor --json` results, compiler identity and diagnostics; avoid unrelated personal files. Distinguish first build, edited build, PNG generation and full verification when reporting performance. Preserve inputs according to [performance measurement](performance.md).

## Submit a change

1. Explain the concrete problem and resulting behavior; keep the scope focused.
2. Update implementation, callers and both documentation languages. Development does not retain legacy syntax or protocol aliases.
3. Select tests covering the affected boundaries; add meaningful positive or negative regression cases.
4. Describe commands, results and remaining limitations in the PR. Old reports do not verify current changes.

Semantic changes must consider text, PNG recovery, execution and debugging provenance. Performance changes must preserve artifacts and diagnostics.

## Choose verification

| Change | Checks to consider |
| --- | --- |
| Documentation | Links, navigation, language counterparts and executable examples |
| Standard library | Types/effects, output, value isolation, callers and image recovery |
| Compiler | Relevant semantics, incremental/fresh equivalence and generated-compiler regression |
| Runtime | Values, resource boundaries, reference/native differentials and replay |
| Studio | Bridge, DAP, extension host; real interaction for dragging or Undo |
| Distribution | Platform-matched wheel/VSIX, isolated installation and artifact identity |

```sh
.venv/bin/python scripts/check_docs.py
.venv/bin/python scripts/verify_preview.py related \
  --test tests.test_stdlib_contracts --output /tmp/pixel-related-NEW
```

See [layered verification](verification.md) for merge and release requirements. Write evidence to a fresh directory outside the checkout so outputs do not mutate inputs.

## Maintain documentation

| Area | Responsibility |
| --- | --- |
| Root README | Scope, shortest useful path and entry points |
| learn | Introductory tutorials with expected results |
| guides | Task steps, prerequisites and troubleshooting |
| reference | Exact rules, signatures, formats and boundaries |
| internals | Architecture, data structures and decisions |
| contributing | Development, verification and measurement |

Give every rule one authoritative home and link to it elsewhere. Keep matching paths in both languages, with language switches and a documentation-home link below each title. Label code blocks, state working directories and prerequisites, and give long pages task-oriented headings and a contents list. Keep migration progress, test totals and individual benchmark runs out of references; do not describe proposals as current features.

Write for the person using the feature: explain what they will be able to do, show a small example, then introduce the terms needed to understand it. Prefer “what you should see” to lists of internal mechanisms. Keep exact constraints in the reference and implementation details in internals; link to them when a reader needs more depth. The repository landing page is English (`README.md`), with Chinese in `README.zh-CN.md`.

The structure follows the separation of learning, guides, references and implementation material in [Rust](https://doc.rust-lang.org/stable/), [Python](https://docs.python.org/3/) and [Go](https://go.dev/doc/) documentation.

## Verify real editor interaction

Use an isolated VS Code environment. Dragging and keyboard Undo need an unlocked desktop and correct focus; successful API calls do not prove interaction. Wait for assertions as described in [verification](verification.md), rather than replacing Undo with direct text changes.
