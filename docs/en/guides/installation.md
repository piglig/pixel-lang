# Install PixelLang

[简体中文](../../zh-CN/guides/installation.md) · [English](installation.md) · [Documentation](../README.md)

Use a toolchain matching your operating system and CPU architecture. Native compiler builds and controlled file access currently target POSIX hosts such as macOS and Linux.

## Requirements

| Task | Dependencies |
| --- | --- |
| Run the toolchain | Python 3.11+, Pillow 10–12 and a matching native compiler bundle |
| Prepare the compiler from source | Git, uv, a C11 compiler, and zlib headers and libraries |
| Build Studio | Node.js and npm; CI uses Node.js 22 |
| Use Studio | VS Code 1.90+ and a Python interpreter with the runtime dependencies |

## Install an existing wheel

If you have a wheel for your platform, install it in an isolated environment. Replace the path with the actual file:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install /absolute/path/to/pixellang.whl
.venv/bin/pixel doctor
```

The wheel includes the native compiler, source map, manifest and standard library. A public package is not guaranteed during development preview; prepare a local bundle when no matching package is available.

## Install from source

### 1. Obtain the sources and Python dependencies

```sh
git clone https://github.com/piglig/pixel-lang.git
cd pixel-lang
uv sync --locked --no-install-project
```

Git contains the compiler bytecode seed, not your platform's native executable. A fresh clone cannot compile programs after `uv sync` alone. `--no-install-project` installs dependencies before the native bundle required by project packaging exists.

### 2. Build and verify the local compiler

The commands below generate a machine-code seed, verify self-compilation and full PNG recovery, then stage four matching bundle files. Full bootstrap is substantially more expensive than compiling an ordinary program. Replace `NEW` with a fresh directory name for each run.

```sh
.venv/bin/python - <<'PYTHON'
import json
from pathlib import Path
from pixellang.native_codegen import build_native, entry_session
code = json.loads(Path("pixellang/artifacts/compiler.json").read_text())
build_native(code, "build/native-seed/compiler", optimization=1,
             max_depth=1024, session=entry_session(code))
PYTHON
.venv/bin/python scripts/verify_native_selfhost.py \
  --seed pixellang/artifacts/compiler.json \
  --native-compiler build/native-seed/compiler \
  --output /tmp/pixel-native-NEW
.venv/bin/python scripts/stage_native_compiler.py \
  --acceptance /tmp/pixel-native-NEW --output /tmp/pixel-bundle-NEW
```

Continue only after verification succeeds. Back up any existing `pixellang/artifacts` directory outside the checkout before replacement.

### 3. Install the verified bundle

```sh
cp /tmp/pixel-bundle-NEW/compiler.json pixellang/artifacts/
cp /tmp/pixel-bundle-NEW/compiler-native pixellang/artifacts/
cp /tmp/pixel-bundle-NEW/compiler-native.map.json pixellang/artifacts/
cp /tmp/pixel-bundle-NEW/compiler-manifest.json pixellang/artifacts/
uv sync --locked
uv run pixel doctor
```

Ordinary builds reuse the prepared compiler; they do not repeat self-hosting verification.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Missing `compiler-native` or platform mismatch | Install a matching wheel or prepare a local bundle |
| Manifest, bytecode or map mismatch | Replace all four bundle files together |
| C compilation fails | Check the C11 toolchain and zlib development files; macOS can use Xcode Command Line Tools |
| Studio cannot find Python or Pillow | Run **PixelLang: Select Python Interpreter** and select the configured environment |

Continue with [your first program](../learn/getting-started.md) or [Studio](studio.md).
