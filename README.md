<p align="center">
  <img src="assets/branding/icon.png" alt="PixelLang 空间版图标" width="160" height="160">
</p>

<h1 align="center">PixelLang</h1>

<p align="center"><strong>用代码编写，以图片交付。</strong></p>
<p align="center">静态类型语言 · 可执行语义像素 · VS Code 工作站</p>

<p align="center">
  <a href="docs/zh-CN/product.md"><img src="https://img.shields.io/badge/status-Development%20Preview-2563eb?style=for-the-badge&logoColor=white" alt="Development Preview" height="20"></a>
  <a href="pyproject.toml"><img src="https://img.shields.io/badge/Python-%E2%89%A5%203.11-3776ab?style=for-the-badge&amp;logo=python&amp;logoColor=white" alt="Python ≥ 3.11" height="20"></a>
</p>

<p align="center">
  <strong>简体中文</strong> · <a href="README.en.md">English</a>
</p>

---

PixelLang 让你用文本编写程序，将程序交付为一张 PNG，再从图片恢复可编辑的多文件项目。颜色、坐标和连接共同表达程序语义，VS Code Studio 将源码、像素和执行过程联系起来。

**[快速上手](#快速上手)** · **[阅读文档](docs/zh-CN/README.md)** · **[使用 Studio](docs/zh-CN/guides/studio.md)** · **[参与开发](docs/zh-CN/contributing/README.md)**

```go
import "std/sort.pxl" as sort

fn main() {
    let values = sort.Ints([9, 2, 7])
    for value in values {
        print(value)
    }
}
```

> **开发预览** · 当前工具链需要 Python 3.11+。图片包含程序及其可达模块，执行时仍需 PixelLang 运行时。恢复源码不保留原始注释与排版。

## 为什么使用 PixelLang

| 能力 | 你可以做什么 |
| --- | --- |
| **图片即程序** | 导出包含语义像素的 PNG，独立传递程序，并恢复为可编辑源码。 |
| **类型明确的语言** | 使用泛型、记录、枚举、`Option`、闭包和结构化错误编写多文件程序。 |
| **算法与数据处理** | 处理 Unicode 文本、数组、映射、CSV 和 JSON，完成排序、分组与统计。 |
| **可观察的执行** | 在 Studio 中设置断点、检查变量、逆向单步，并沿时间轴查看执行状态。 |

图片读取直接解码像素，不使用 OCR，也不把完整文本源码或字节码藏在元数据中执行。文本编译可以直接生成字节码，无需先导出图片。

## 快速上手

准备 **Python 3.11+** 和 **[uv](https://docs.astral.sh/uv/getting-started/installation/)**，然后获取项目：

```sh
git clone https://github.com/piglig/pixel-lang.git
cd pixel-lang
uv sync --locked
```

将上面的示例保存为 `main.pxl`，运行：

```sh
uv run pixelrun main.pxl
```

输出：

```text
2
7
9
```

### 交付与恢复

```sh
# 将源码打包为可执行图片
uv run pixelpack main.pxl -o program.png

# 直接运行图片中的程序
uv run pixelrun program.png

# 将图片恢复为可编辑项目，目标目录应为空
uv run pixelunpack program.png -o recovered
```

PNG 包含所有可达模块，运行它不需要原来的 `.pxl` 文件。请保留精确像素，避免缩放、有损压缩或色彩变换。了解更多：[项目与依赖](docs/zh-CN/guides/projects.md) · [可执行 PNG 格式](docs/zh-CN/reference/formats/png.md)。

## 在 VS Code 中开发

安装扩展后，执行 **PixelLang: Open Studio**，或打开 `.pxl` 文件按 **F5** 调试。

- **编写**：实时诊断、补全、定义与引用、重命名和格式化。
- **构建**：项目测试、PNG 导出与恢复、按需查看编译器 IR。
- **调试**：断点、调用栈、分页变量、逆向单步和时间记录。
- **探索**：二维语义编辑，以及以执行时间为 Z 轴的三维视图。

从源码构建扩展需要 Node.js 和 npm。在仓库根目录执行：

```sh
npm --prefix vscode ci
npm --prefix vscode run package
```

在 VS Code 的 **Extensions → Install from VSIX** 中选择生成的扩展包。Python 环境需安装 Pillow；前面的 `uv sync --locked` 已安装运行依赖。

[Studio 安装、配置与操作指南 →](docs/zh-CN/guides/studio.md)

## 示例项目

| 示例 | 内容 | 运行测试 |
| --- | --- | --- |
| [数据报告](examples/data-report/main.pxl) | CSV、排序、统计 | `uv run pixel test examples/data-report` |
| [回调分析](examples/callback-analysis/main.pxl) | 闭包、筛选、分组、稳定排序 | `uv run pixel test examples/callback-analysis` |
| [Ledger](examples/ledger/README.md) | 文件读写、数据校验、分类报表 | `uv run pixel test examples/ledger` |
| [日志分析](examples/log-analysis/README.md) | 多文件 JSON Lines、时间筛选、聚合 | `uv run pixel test examples/log-analysis` |

## 文档导航

| 你想了解 | 从这里开始 |
| --- | --- |
| 项目定位与能力边界 | [产品说明](docs/zh-CN/product.md) |
| 语言语法与数据规则 | [语言参考](docs/zh-CN/reference/language/overview.md) · [标准库](docs/zh-CN/reference/standard-library.md) |
| 构建项目、管理依赖与测试 | [项目指南](docs/zh-CN/guides/projects.md) · [命令行工具](docs/zh-CN/guides/cli.md) |
| 编译器如何工作 | [架构](docs/zh-CN/internals/architecture.md) · [自举](docs/zh-CN/internals/self-hosting.md) · [Python 的职责](docs/zh-CN/internals/python.md) |
| 全部文档 | [简体中文](docs/zh-CN/README.md) · [English](docs/en/README.md) |

编译器由 PixelLang 编写，当前运行在 Python 实现的 PixelVM 上。源码采用二维空间模型，Studio 的显示 Z 轴表示时间；完整三维源码、网络 I/O 和原生机器码/WASM 后端尚未支持。

## 参与开发

欢迎提交可复现的问题、文档改进和代码贡献。开始前请阅读[贡献指南](docs/zh-CN/contributing/README.md)，为修改选择合适的验证范围。

```sh
# 检查双语文档、导航与链接
uv run python scripts/check_docs.py

# 运行明确选择的相关测试，结果写入新的外部目录
uv run python scripts/verify_preview.py related \
  --test tests.test_compiler_service --output /tmp/pixel-related-NEW
```

合并检查、完整自举、图片往返和真实编辑器验收分别运行，详见[分层验证](docs/zh-CN/contributing/verification.md)。文档同时维护中文和英文版本。

## 许可证

PixelLang 采用 [Apache License 2.0](LICENSE)。
