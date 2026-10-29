<p align="center">
  <img src="assets/branding/icon.png" alt="PixelLang" width="128" height="128">
</p>

<h1 align="center">PixelLang</h1>

<p align="center"><strong>让空间参与计算，让图片承载程序。</strong></p>

<p align="center">
  <a href="docs/zh-CN/product.md"><img src="https://img.shields.io/badge/status-development_preview-2563eb" alt="Development preview"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-64748b" alt="Apache-2.0"></a>
</p>

<p align="center"><a href="README.md">English</a> · <a href="README.zh-CN.md">简体中文</a></p>

PixelLang 是一门空间编程语言。你可以用它处理位置、连接和不断变化的状态，再把程序保存成一张 PNG，分享给别人运行，也可以从图片恢复代码，继续修改。

**[安装](docs/zh-CN/guides/installation.md)** · **[快速上手](docs/zh-CN/learn/getting-started.md)** · **[文档](docs/zh-CN/README.md)** · **[VS Code Studio](docs/zh-CN/guides/studio.md)**

> PixelLang 目前处于开发预览阶段，欢迎试用。语法和文件格式仍在完善；运行 PNG 中的程序需要安装 PixelLang 运行时。

## 看一个程序

```pixellang
import "std/collections.pxl" as collections

effect fn main() {
    let grouped = collections.GroupBy([1, 2, 3, 4], fn(value: int) = value % 2)
    print(grouped[1])
}
```

这个程序按除以二的余数分组，再取出奇数，输出 `[1, 3]`。你可以先从排序、分组、读取文件这些熟悉的任务开始，需要时再使用空间能力。

## 可以用它做什么？

- **让程序理解空间。** 找到附近的实体，在三维空间中移动它们，或者为实体间的连接设置成本和状态。
- **让程序响应事件。** 描述订单状态的变化，或让控制器处理输入。`flow` 会记住上一步的状态，供下一步使用。
- **把程序分享成图片。** 导出 PNG，用新的输入运行它，或者恢复源码，接着开发。

在 VS Code Studio 中，你还可以设置断点、查看变量，并回看程序在不同步骤中的状态。

## 运行与交付

先按[安装指南](docs/zh-CN/guides/installation.md)配置工具链，再将程序保存为 `main.pxl`：

```sh
pixelrun main.pxl
pixelpack main.pxl -o program.png
pixelrun program.png
pixelunpack program.png -o recovered
```

现在你有了一张可以分享的图片，也有了一份可以继续编辑的源码。请为 `recovered` 选择空文件夹，并直接分享原始 PNG，不要缩放或编辑图片。恢复后的代码行为一致，但不会保留原有注释和排版。

## 在 VS Code 中开发

安装匹配平台的 VSIX，选择已配置的 Python 解释器，执行 **PixelLang: Open Studio**。使用 F5 进入源码调试；通过 **Open Native Graph** 检查计算依赖和空间值。

安装、配置、导出与调试流程见 [Studio 指南](docs/zh-CN/guides/studio.md)。

## 示例

| 项目 | 学习内容 |
| --- | --- |
| [订单状态机](examples/native-orders/README.md) | 类型化事件、业务拒绝与状态转移 |
| [三维邻域](examples/native-space/README.md) | Z 坐标如何影响程序输出 |
| [带权路径](examples/weighted-routes/README.md) | 连接负载和逐步传播 |
| [空间电梯](examples/native-elevator/README.md) | 多文件控制器、空间快照与独立实例 |
| [Ledger](examples/ledger/README.md) | CSV 校验、分组、排序和报告 |
| [日志分析](examples/log-analysis/README.md) | 流式文件读取和类型化聚合 |

## 开始之前

工具链目前支持 macOS 和 Linux，需要 Python 3.11+ 和匹配平台的原生编译器包。[安装指南](docs/zh-CN/guides/installation.md)会带你完成配置。

PixelLang 仍在发展中：目前使用本地依赖，尚未提供网络 API。如果你正在评估它是否适合自己的项目，可以先看[项目介绍](docs/zh-CN/product.md)。如果你好奇编译器如何工作，它本身也是用 PixelLang 编写的，[架构指南](docs/zh-CN/internals/architecture.md)会介绍实现方式。

## 参与贡献

遇到了不清楚的示例，或与预期不同的运行结果？欢迎反馈问题、改进文档或贡献代码。[贡献指南](docs/zh-CN/contributing/README.md)会帮你找到起点。

## 许可证

[Apache License 2.0](LICENSE)。
