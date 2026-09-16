# 参与开发

[中文](README.md) · [English](../../en/contributing/README.md) · [文档首页](../README.md)

在仓库根执行 uv sync --locked，扩展依赖用 npm --prefix vscode ci。
先读[架构](../internals/architecture.md)和[Python 职责](../internals/python.md)。

## 验证变更

- 文档：`.venv/bin/python scripts/check_docs.py`，并运行变更的可执行教程。
- 代码：使用[相关门禁](verification.md)，显式选择测试；合并与发布用对应层级。
- 性能：记录同输入、源码/产物身份、电源、墙钟、VM 指令和内存，见[测量](performance.md)。
- 分发：uv build 后运行 tests/clean_install.py；扩展打包和真实编辑器测试独立进行。

## 文档维护

docs/zh-CN 与 docs/en 使用相同相对路径，页面顶部可切换语言。
入门放 learn，操作步骤放 guides，规则放 reference，实现放 internals，开发流程放 contributing。
产品范围以 product.md 为准。修改规则时同时更新两种语言；每条规则在每种语言中只有一个权威位置，其他页链接引用。
不保留历史计划、进展日志、发布验收报告或基准结果副本。测量输出写仓库外，当前配置放 config，代码和夹具留在对应目录。
不要把实验、设计目标或某次测试通过写成普遍支持声明。

## 真实编辑器

实际 VS Code 扩展宿主测试需要解锁桌面。撤销等命令依赖真实焦点；后台 API 成功不能证明交互成功。
测试需聚焦隔离窗口、执行撤销并等待源码恢复，不应删除断言或直接改写文本代替验证。
