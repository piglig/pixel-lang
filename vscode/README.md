# PixelLang Studio

[简体中文](README.md) · [English](README.en.md) · [完整指南](../docs/zh-CN/guides/studio.md)

在 VS Code 中编写、运行和调试 PixelLang，查看三维空间与执行历史。

## 功能

- 诊断、补全、格式化、定义、引用与重命名。
- 项目测试、构建、可执行 PNG 导出和源码恢复。
- 断点、调用栈、分页变量、逆向单步与记录回放。
- flow 依赖图、实体和连接检查，以及可撤销的源码坐标编辑。

## 安装

需要 VS Code 1.90+、Python 3.11+、Pillow 10–12 和匹配平台的 VSIX。
通过 **Extensions: Install from VSIX** 安装，再运行 **PixelLang: Select Python Interpreter** 和 **PixelLang: Check Environment**。

## 开始使用

打开 `.pxl` 文件，执行 **PixelLang: Open Studio**，或按 F5 调试。
空间程序使用 **PixelLang: Open Native Graph**，事件应用使用 **PixelLang: Open Native Application**。
文件读写需设置 `pixellang.readRoot` 和 `pixellang.writeRoot`；项目入口由 `pixel.toml` 或 `pixellang.entry` 指定。

## 从源码构建

先按[安装指南](../docs/zh-CN/guides/installation.md)准备原生编译器，在仓库根执行：

```sh
npm --prefix vscode ci
npm --prefix vscode run package
```

打包不会发布扩展。项目处于开发预览，不提供旧协议兼容。许可为 [Apache-2.0](../LICENSE)。
