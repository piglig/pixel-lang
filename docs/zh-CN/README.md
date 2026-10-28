# PixelLang 文档

[简体中文](README.md) · [English](../en/README.md) · [文档首页](README.md)

从一个可运行的程序开始，再按任务查阅指南。需要精确行为时使用参考手册；修改编译器时阅读内部实现与贡献流程。

| 你的目标 | 推荐入口 |
| --- | --- |
| 第一次尝试 | [安装](guides/installation.md) → [第一个程序](learn/getting-started.md) |
| 开发应用 | [项目](guides/projects.md) → [Studio](guides/studio.md) |
| 使用语言特色 | [空间编程](guides/spatial-programming.md) · [状态机](guides/state-machines.md) |
| 贡献代码 | [架构](internals/architecture.md) → [贡献指南](contributing/README.md) |

## 开始使用

- [项目定位与边界](product.md)
- [安装工具链](guides/installation.md)
- [第一个程序](learn/getting-started.md)

## 完成任务

- [项目、依赖与测试](guides/projects.md)
- [VS Code Studio](guides/studio.md)
- [命令行工具](guides/cli.md)
- [三维空间编程](guides/spatial-programming.md)
- [状态机与事件应用](guides/state-machines.md)

## 查询语言规则

- [语法总览](reference/language/overview.md)
- [数值](reference/language/numbers.md)
- [值与修改权限](reference/language/data.md)
- [函数、效果与闭包](reference/language/functions.md)
- [泛型与能力约束](reference/language/generics.md)
- [枚举、Option 与模式匹配](reference/language/enums.md)
- [Result 与错误传播](reference/language/errors.md)
- [类型化 JSON](reference/language/json.md)
- [集合操作与算法](reference/language/collections.md)
- [空间与源码布局](reference/language/spatial.md)

## API 与格式

- [标准库](reference/standard-library.md)
- [编译服务协议](reference/compiler-service.md)
- [文件类型](reference/formats/files.md)
- [语义像素编码](reference/formats/encoding.md)
- [可执行 PNG](reference/formats/png.md)
- [PixelVM 字节码](reference/formats/bytecode.md)
- [运行时与回放](reference/formats/runtime.md)

## 理解实现

- [架构](internals/architecture.md)
- [设计原则与后续方向](internals/design.md)
- [编译器接口](internals/compiler-interfaces.md)
- [IR 与函数复用](internals/ir.md)
- [值存储](internals/value-storage.md)
- [空间模型实现](internals/spatial-model.md)
- [数据流与事务](internals/dataflow.md)
- [Python 的职责](internals/python.md)
- [自举与源码恢复](internals/self-hosting.md)

## 参与贡献

- [贡献指南](contributing/README.md)
- [分层验证](contributing/verification.md)
- [一致性覆盖](contributing/conformance.md)
- [性能测量](contributing/performance.md)
