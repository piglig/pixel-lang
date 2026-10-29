# 安装 PixelLang

[简体中文](installation.md) · [English](../../en/guides/installation.md) · [文档首页](../README.md)

先在电脑上把 PixelLang 跑起来。你可以安装适合自己电脑的软件包，也可以从源码构建。目前支持 macOS 和 Linux；选择软件包时，请确认操作系统和 CPU 架构与本机一致。

## 环境要求

| 用途 | 依赖 |
| --- | --- |
| 运行工具链 | Python 3.11+、Pillow 10–12、匹配平台的原生编译器包 |
| 从源码准备编译器 | Git、uv、C11 编译器、zlib 头文件与库 |
| 构建 Studio | Node.js、npm；CI 使用 Node.js 22 |
| 使用 Studio | VS Code 1.90+，以及满足运行依赖的 Python 解释器 |

## 安装已有 wheel

如果已经取得匹配平台的 wheel，在独立环境中安装。将下面的路径替换为实际文件：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install /absolute/path/to/pixellang.whl
.venv/bin/pixel doctor
```

wheel 包含原生编译器、源码映射、清单和标准库。开发预览不保证已有公开发布包；没有匹配包时按下节从源码准备。

## 从源码安装

### 1. 获取源码与 Python 依赖

```sh
git clone https://github.com/piglig/pixel-lang.git
cd pixel-lang
uv sync --locked --no-install-project
```

Git 保存编译器字节码种子，不保存本机原生可执行文件。首次克隆不能只运行 `uv sync` 就直接编译程序。
`--no-install-project` 先安装依赖，避免在原生包就绪前构建项目。

### 2. 构建并验证本机编译器

以下命令先将种子生成为机器码，再验证自编译固定点和完整 PNG 恢复，最后暂存匹配的四个文件。
这一步包含完整自举，耗时显著高于编译普通程序。每次将 `NEW` 替换为新的目录名。

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

只有验证成功后才能继续。若已有本地产物，先将 `pixellang/artifacts` 备份到仓库外。

### 3. 安装已验证的产物

```sh
cp /tmp/pixel-bundle-NEW/compiler.json pixellang/artifacts/
cp /tmp/pixel-bundle-NEW/compiler-native pixellang/artifacts/
cp /tmp/pixel-bundle-NEW/compiler-native.map.json pixellang/artifacts/
cp /tmp/pixel-bundle-NEW/compiler-manifest.json pixellang/artifacts/
uv sync --locked
uv run pixel doctor
```

日常编译复用已准备的原生编译器，不会再次执行完整自举。

## 常见安装问题

| 现象 | 检查方法 |
| --- | --- |
| 缺少 `compiler-native` 或平台不匹配 | 使用对应平台的 wheel，或重新执行本机准备流程 |
| 清单、字节码或映射校验失败 | 整组替换四个产物文件，不混用不同构建 |
| C 编译失败 | 检查 C11 工具链及 zlib 开发文件；macOS 可使用 Xcode Command Line Tools |
| Studio 找不到 Python 或 Pillow | 执行 **PixelLang: Select Python Interpreter**，选择已安装依赖的解释器 |

安装成功后继续[第一个程序](../learn/getting-started.md)，或[安装 Studio](studio.md)。
