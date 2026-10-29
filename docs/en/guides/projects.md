# Projects, dependencies and tests

[简体中文](../../zh-CN/guides/projects.md) · [English](projects.md) · [Documentation](../README.md)

As your program grows, you can split it into files, reuse local libraries and add tests. This guide shows how to keep those pieces together with a `pixel.toml` project file. Complete [installation](installation.md) before trying the commands.


**On this page**

- [Create a project](#create-a-project)
- [Commands](#commands)
- [Manifest format 1](#manifest-format-1)
- [Local libraries and integrity](#local-libraries-and-integrity)
- [Verification](#verification)
- [File-processing test fixtures](#file-processing-test-fixtures)
- [VS Code project commands](#vs-code-project-commands)
- [Project compilation cache](#project-compilation-cache)
- [Further reading](#further-reading)

## Create a project

```sh
pixel doctor
pixel init hello-pixels
cd hello-pixels
pixel run .
pixel test .
pixel build .
```

Use `--name` to specify a project name. Creation refuses existing files and does not download dependencies. Controlled compiler file access currently requires a POSIX host such as Linux or macOS.

## Commands

Install the Python package, then use the unified `pixel` command (or `python -m pixellang`):

```sh
pixel lock examples/data-report
pixel test examples/data-report
pixel build examples/data-report
pixel run examples/data-report --input examples/data-report/measurements.csv
pixel run examples/data-report/dist/program.png --input examples/data-report/measurements.csv
```

Project commands default to the current directory. `build` writes `dist/program.png`, or the path given with `-o`. The build result includes the artifact SHA-256. `run` also accepts the original source/image/bytecode formats. `test` returns a machine-readable summary and exit code 1 if any test fails; an empty test suite is an error. `--max-steps` applies independently to each test.

## Manifest format 1

```toml
format = 1

[project]
name = "report"
entry = "main.pxl"

[dependencies]
math = "../math-library"

[[test]]
name = "sample report"
entry = "main.pxl"
input = "fixtures/input.csv"
expected = "fixtures/output.json"
```

`expected` is a JSON array containing the program's printed values in order. Integer and boolean values are distinct. Tests without an expected file use `assert` to check results. Programs at `tests/*_test.pxl` (including nested directories) are discovered automatically and each runs in a fresh VM. Explicit test entries also allow multiple input fixtures for one entry program.

All source names, test inputs and expected-output paths stay inside the project. `.git`, `.venv`, `node_modules`, `dist`, `__pycache__` and hidden directories are excluded from source discovery. The project plus its dependencies is currently limited to 128 source files, with at most 4 MB per source file and the existing one-million-character parser limit. Imports retain normal lexical relative-path semantics.

Test files may also declare multiple `fn test_name() { ... }` functions. They must take no parameters,
have no type parameters and return unit; the file must not declare main. Each function runs independently
as `tests/example_test.pxl::test_name`. Files without test_ functions still run through main.
The native compiler discovers these declarations and selects each test as the program
entry without inserting a generated `main`. Discovery runs only when tests are requested;
ordinary project builds, runs and lock operations do not parse test files for discovery.
Use `pixel test . --list` to list tests and `pixel test . --filter '*::test_name'` to select them.
A filter matching no tests is an error.

## Local libraries and integrity

A dependency is an explicitly declared local project with its own `pixel.toml`. Version 1 accepts relative paths to projects outside the declaring project's source root; it has no network registry or implicit downloads. `deps/` is a reserved virtual namespace, not a directory to populate manually.

For the `math` alias above, import `"deps/math/main.pxl"` from a root-level module. From `src/main.pxl`, import `"../deps/math/main.pxl"`. A library can declare its own dependencies, mounted inside its namespace; cycles and more than 16 levels are rejected.

`pixel lock` writes `pixel.lock` with SHA-256 hashes of each dependency manifest and all its source files, including nested dependencies. Builds, runs and tests reject missing or stale dependency locks. A dependency-free project can run without a lock. Local application source changes do not require relocking. Lock updates are explicit and should be reviewed with dependency changes.

Lock paths and compiled module identities are relative. Images contain reachable modules, require no dependency directories at execution time, and do not embed absolute source paths. Same sources, lock, compiler, image scale and encoder environment produce byte-identical images; tests exercise relocation across directories. Cross-version PNG encoder byte stability is not promised.

## Verification

Use `pixel test` for project tests. Toolchain packaging and isolated installation belong to [contributor verification](../contributing/verification.md).

## File-processing test fixtures

A `[[test]]` may declare `read_root = "fixtures"` to grant reads inside a project subdirectory. `written = { "report.json" = "fixtures/report.json" }` grants writes to a fresh temporary directory and verifies exact output-file bytes against the named fixture. Each test gets a new VM and output directory. Running a project or PNG outside the test harness still requires explicit CLI directory grants. Ledger exercises this contract with three input/output scenarios.

## VS Code project commands

Open a folder containing `pixel.toml` (or a parent folder), then open a source
module. Studio resolves the nearest manifest and uses its entry even when editing
a helper module. Local dependency sources use the same logical module names and
lock rules as the CLI; editor buffers overlay saved source for analysis.

The command palette provides:

- **PixelLang: Run a Test** saves the project and lets you select a test to run, including discovered test_ functions.
- **PixelLang: Save and Test Project** — saves project documents and runs the
  manifest/auto-discovered tests, reporting each result in PixelLang output.
- **PixelLang: Save and Build Project PNG** — saves, checks the lock and builds
  `dist/program.png`, reporting its absolute path and SHA-256.
- **PixelLang: Save and Update Dependency Lock** — explicitly saves and regenerates
  `pixel.lock` after dependency changes have been reviewed.

These commands save documents belonging to the project and its mounted sources
and manifests. Ordinary Studio builds reject unsaved manifests or dependency
changes, and never silently regenerate a lock. Editor analysis can still inspect
unlocked dependency changes. Configuration parsing currently uses saved manifests.

## Project compilation cache

`pixel run` and `pixel test` reuse project bytecode from the user cache. Every execution still creates a new VM,
reads current inputs and performs granted file operations. Keys cover all sources, standard-library content,
the compiler, runtime code and Python version; dependency locks are always checked first.
Corrupt or unwritable caches fall back to compilation. At most 32 entries and 128 MB are retained.
Set `PIXELLANG_DISABLE_CACHE=1` to force compilation, or `PIXELLANG_CACHE_DIR` for an isolated cache directory.
By default the cache is stored under the current user's cache directory; project-supplied cache artifacts are not loaded.

## Further reading

- [Documentation index](../README.md)
- [Install the toolchain](installation.md)
