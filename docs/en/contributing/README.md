# Contributing

[中文](../../zh-CN/contributing/README.md) · [English](README.md) · [Documentation](../README.md)

Run uv sync --locked at the repository root; use npm --prefix vscode ci for extension
dependencies. Read [architecture](../internals/architecture.md) and [Python responsibilities](../internals/python.md).

## Validate changes

- Documentation: run `.venv/bin/python scripts/check_docs.py` and execute changed runnable tutorials.
- Code: select explicit [related tests](verification.md); use merge/release gates for those scopes.
- Performance: record matching inputs, source/artifact identity, power, wall time, VM steps and memory; see [measurement](performance.md).
- Distribution: after uv build run tests/clean_install.py; extension packaging and real-editor acceptance are separate.

## Maintain documentation

docs/zh-CN and docs/en have identical relative paths and language switches on every
page. Use learn for tutorials, guides for procedures, reference for rules, internals
for implementation and contributing for development. product.md defines scope.
Update both languages together. Each rule has one authoritative location per language;
other pages link to it. Do not retain historical plans, progress logs, release acceptance
reports or benchmark result copies. Write run outputs outside the repository, current
configuration under config, and code/fixtures in their own directories. Experiments,
design goals or individual passing tests must not become universal support claims.

## Real editor tests

VS Code extension-host interaction tests need an unlocked desktop. Undo and similar
commands rely on actual focus; successful background APIs do not prove interaction.
Focus the isolated window, execute Undo and wait for source restoration. Do not remove
assertions or directly rewrite text instead of testing the interaction.
