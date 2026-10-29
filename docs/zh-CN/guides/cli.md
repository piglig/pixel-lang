# 命令行工具

[简体中文](cli.md) · [English](../../en/guides/cli.md) · [文档首页](../README.md)

你可以在终端运行程序、生成图片，也可以从 PNG 恢复代码。在下方找到你要完成的操作；想查看某个命令的全部选项，可以加上 `--help`。

## 选择命令

| 命令 | 用途 |
| --- | --- |
| pixel init [目录] | 创建包含示例与测试的项目；目标目录必须为空 |
| pixel doctor [--json] | 检查 Python、宿主、Pillow PNG 编解码与编译器校验和 |
| pixel lock/test/build/run | 清单项目依赖、测试、构建和运行 |
| pixelc | 编译源码或图片 |
| pixelrun | 运行源码、图片或字节码 |
| pixelinspect | 检查原生 source、debug、graph、ir、bytecode |
| pixelfmt | 文本格式化 |
| pixelrender / pixelpack | 生成图片 |
| pixelunpack | 从交付 PNG 恢复项目 |
| pixeldebug | 调试工具入口 |
| pixelcompiler | JSON-lines 编译服务 |

各命令 --help 列出参数。运行输入为独立字符串/文件；数据文件访问需要显式读写根授权。
错误写 stderr 并返回非零，支持的 --json 输出结构化诊断。
[项目指南](projects.md)说明 pixel.toml、依赖锁与测试。
Studio 唯一宿主是 VS Code，无 pixelstudio 命令或浏览器服务；见[工作站](studio.md)。

## 检查编译产物

`pixelinspect main.pxl --stage ir` 使用附带的 PixelLang 编译器，不调用 Python 编译前端。
源码支持 `source`（语义像素文档）、`debug`（调试映射）、`graph`（原生空间与数据流图）、
`ir` 和 `bytecode`。默认 `all` 返回 source、debug、ir、bytecode。
PNG、`.pixel`/`.json` 语义文档和 `.pxb` 支持 `all` 或 `bytecode`，前者返回包含 bytecode 字段的对象；
其他检查阶段需要 `.pxl` 源码。`.pxb` 在输出前验证格式和指令。
`-o` 指定 JSON 输出文件，检查失败不会覆盖已有文件。旧 spatial_ast／canonical_ast 阶段已移除。

语义像素文档的编译、运行和调试也使用原生编译器的像素解码与类型检查。
多模块文档通过 bundle 携带依赖，这些命令不会回退到 Python 前端。

像素文档和 PNG 的 `pack`、`render`、`unpack` 也使用原生编译器。
像素文档格式化会重新生成规范语义布局；`.pxl` 文本格式化则保留注释和字面量拼写。
多模块恢复使用 `unpack -o 目录`；把多模块文档格式化为单个 `.pxl` 文件会报错，避免丢失依赖。

## 格式化源码

`pixelfmt main.pxl -o formatted.pxl` 使用原生编译器的语法格式化器。
保留已有换行、注释和字面量拼写，统一水平间距，并按括号层级使用四空格缩进。
格式化前后均进行解析，确认语法结构不变；不要求导入文件存在，也不进行项目类型检查。
输出可以与输入为同一文件；语法错误不会覆盖已有输出。

## 继续阅读

- [完整文档目录](../README.md)
- [安装工具链](installation.md)
