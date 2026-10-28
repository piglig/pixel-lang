<p align="center">
  <img src="assets/branding/icon.png" alt="PixelLang" width="128" height="128">
</p>

<h1 align="center">PixelLang</h1>

<p align="center"><strong>让空间参与计算，让图片承载程序。</strong></p>

<p align="center">
  <a href="docs/zh-CN/product.md"><img src="https://img.shields.io/badge/status-development_preview-2563eb" alt="Development preview"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-64748b" alt="Apache-2.0"></a>
</p>

<p align="center"><a href="README.md">简体中文</a> · <a href="README.en.md">English</a></p>

PixelLang 是一门静态类型的空间编程语言：用文本编写算法与状态流程，让三维实体、坐标和连接参与计算，再将程序交付为可恢复源码的 PNG。

**[安装](docs/zh-CN/guides/installation.md)** · **[快速上手](docs/zh-CN/learn/getting-started.md)** · **[文档](docs/zh-CN/README.md)** · **[VS Code Studio](docs/zh-CN/guides/studio.md)**

> 开发预览：语法和格式可能发生不兼容变更。执行 PNG 仍需 PixelLang 运行时；图片不是截图，也不是独立机器码程序。

## 看一个程序

```pixellang
import "std/collections.pxl" as collections

effect fn main() {
    let grouped = collections.GroupBy([1, 2, 3, 4], fn(value: int) = value % 2)
    print(grouped[1])
}
```

输出 `[1, 3]`。数组、映射和记录采用值语义，泛型推导让常见调用保持简洁。

## 为什么使用 PixelLang

| 能力 | 含义 |
| --- | --- |
| 空间参与计算 | 坐标系、三维邻域、方向和类型化连接是程序语义 |
| 显式状态转移 | flow 读取旧状态，成功后原子提交下一状态 |
| 图片交付与恢复 | PNG 携带入口和可达模块，可恢复多文件源码 |
| 可观察执行 | Studio 提供断点、变量、空间检查与历史回放 |
| 自举编译器 | 编译器用 PixelLang 编写，日常构建调用原生产物 |

## 运行与交付

先按[安装指南](docs/zh-CN/guides/installation.md)配置工具链，再将程序保存为 `main.pxl`：

```sh
pixelrun main.pxl
pixelpack main.pxl -o program.png
pixelrun program.png
pixelunpack program.png -o recovered
```

恢复目录必须为空。图片保存程序含义，不保留原注释与排版；传输时保持精确像素。首次源码安装需要准备平台原生编译器，不只是安装 Python 依赖。

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

## 项目边界

当前工具链面向 macOS／Linux 等 POSIX 环境，需要 Python 3.11+ 和匹配平台的原生编译器。Python PixelVM 提供常规应用运行与调试；原生代码生成器用于编译器及验证。依赖为本地项目，文件权限显式授予，没有网络包注册表或语言级网络 I/O。

更多信息见[项目定位](docs/zh-CN/product.md)和[架构](docs/zh-CN/internals/architecture.md)。

## 参与贡献

欢迎提交可复现的问题、文档改进和代码变更。请先阅读[贡献指南](docs/zh-CN/contributing/README.md)。中英文文档同步维护，测试按变更范围选择。

## 许可证

[Apache License 2.0](LICENSE)。
