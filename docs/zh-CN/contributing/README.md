# 贡献指南

[简体中文](README.md) · [English](../../en/contributing/README.md) · [文档首页](../README.md)

欢迎提交问题、文档改进和代码变更。优先解决一个清晰的问题，并提供可复现的输入和验证结果。

## 准备开发环境

先完成[源码安装](../guides/installation.md)，再安装扩展依赖：

```sh
npm --prefix vscode ci
```

理解项目分工可从[架构](../internals/architecture.md)、[编译器接口](../internals/compiler-interfaces.md)和[Python 职责](../internals/python.md)开始。

## 报告问题

在 GitHub Issue 中提供最小源码、执行命令、预期与实际行为、系统与 Python 版本。
编译或图片问题应附 `pixel doctor --json` 的相关结果、编译器身份与诊断；不要上传不相关的个人文件。
性能问题需区分首次构建、修改后构建、PNG 生成与完整验证，并按[性能指南](performance.md)保留可复现输入。

## 提交变更

1. 说明具体问题与修改后的行为，控制变更范围。
2. 更新实现、调用方与对应中英文文档。开发阶段不维护旧语法或协议兼容入口。
3. 选择覆盖变更边界的测试；补充能防止问题复发的正例或负例。
4. 在 PR 中说明验证命令、结果和剩余限制，不用旧报告代替当前验证。

语义变更同时考虑文本、PNG 恢复、执行与调试来源。性能改动必须保持产物和诊断正确性。

## 选择验证范围

| 变更 | 最低检查方向 |
| --- | --- |
| 文档 | 链接、导航、中英文对应及可执行示例 |
| 标准库 | 类型与效果、运行结果、值隔离、调用方和图片恢复 |
| 编译器 | 相关语义、增量／全量等价、生成编译器回归 |
| 运行时 | 值与资源边界、参考／原生差分、调试回放 |
| Studio | Bridge、DAP、扩展宿主；涉及拖动／撤销时使用真实交互 |
| 分发 | 匹配平台的 wheel／VSIX、隔离安装和产物身份 |

```sh
.venv/bin/python scripts/check_docs.py
.venv/bin/python scripts/verify_preview.py related \
  --test tests.test_stdlib_contracts --output /tmp/pixel-related-NEW
```

合并与发布要求见[分层验证](verification.md)。证据写入新的仓库外目录，避免输出改变被测输入。

## 维护文档

| 文档区域 | 职责 |
| --- | --- |
| 根 README | 项目定位、最短使用路径和入口 |
| learn | 带预期结果的入门教程 |
| guides | 完成具体任务的步骤、前提和排错 |
| reference | 精确规则、签名、格式与边界 |
| internals | 架构、数据结构与设计取舍 |
| contributing | 开发、测试和测量流程 |

每条规则只设一个权威位置，其他页面链接引用。中英文使用相同相对路径，标题下保留语言切换和文档首页。
代码示例标注语言；Shell 命令说明工作目录和前提；长文使用任务化标题与页面目录。
不在参考手册散布迁移进度、测试数量或单次跑分，不把计划写成当前能力。

本结构参考 [Rust 文档](https://doc.rust-lang.org/stable/)、[Python 文档](https://docs.python.org/3/)和 [Go 文档](https://go.dev/doc/)对学习材料、使用指南、参考与实现资料的分工。

## 真实编辑器验证

桌面交互测试使用隔离的 VS Code 环境。拖动和键盘撤销需要解锁桌面与正确焦点；API 成功不等于交互成功。
按[验证流程](verification.md)等待实际断言完成，不用直接改写文本替代 Undo。
