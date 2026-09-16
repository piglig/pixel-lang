# 分层验证

[中文](verification.md) · [English](../../en/contributing/verification.md) · [文档首页](../README.md)

在仓库根使用 .venv/bin/python，结果写入仓库外的新目录，避免输出改变被测输入。
测试失败、子证据不完整、超时或输入哈希变化均导致门禁非零退出。

## 日常相关修改

```sh
.venv/bin/python scripts/verify_preview.py related --output /tmp/pixel-related-NEW --test tests.test_compiler_service
```

至少显式指定一个模块/类/方法，仅验证选择范围，不隐式执行完整自举或宣称全项目验收。

## 合并前

```sh
.venv/bin/python scripts/verify_preview.py merge --workers 2 --output /tmp/pixel-merge-NEW
```

运行宿主语言/项目测试（非 test_selfhost* 模块）、生成编译器的检查/IR/恢复/服务/缓存测试、
真实 Node Bridge、无界面 DAP 和安装服务性能预算。安装产物与清单必须匹配当前编译器源码。
完整编译器源码检查/降低留给自举；发布另需真实 VS Code 扩展宿主测试。

## 发布前

```sh
.venv/bin/python scripts/verify_preview.py release --workers 2 --output /tmp/pixel-release-NEW
```

包含合并门禁，再执行三代完整产物比较、完整无缓存编译器 PNG 导出/恢复、生成编译器读图、恢复编译器回归。
安装产物必须与接受的 stage3 一致。此命令成本高，日常用 related。
编译器发布门禁不替代 wheel/VSIX 安装、真实编辑器和对应同一快照的发布检查。

## 性能预算

```sh
.venv/bin/python scripts/benchmark_service.py --output /tmp/pixel-performance-NEW.json --limits config/service-performance-limits.json
```

固定双文件负载测冷启动/检查、热检查、修改、编译、PNG 和子进程峰值内存，恢复前后都验证执行。
阈值是回归上限，留出机器噪声空间，不承诺 UI 延迟，也不代表完整编译器负载。
同时限制时间与 VM 指令；内存为已回收子进程高水位，按 macOS 字节/Linux KiB 归一化。
记录电源与低电量设置，正式对照须同条件、同负载。
门禁测试包含故意失败的指令预算、真实测试失败传播、输出目录保护、非有限/不完整阈值拒绝。

## 恢复已完成阶段

```sh
.venv/bin/python scripts/verify_preview.py merge --workers 2 \
  --resume-from /tmp/pixel-merge-PREVIOUS --output /tmp/pixel-merge-RESUMED
```

必须使用新目录。源码/工具/测试哈希、门禁选择、并行度、Python/平台/依赖及规范命令必须相同。
只复制完成且成功、全部输出哈希完整的阶段，损坏/缺失或变化输入不复用，失败阶段重跑。
摘要记录 reusedFrom、originalSeconds；源输出不改写。
性能总是重跑，fresh: false 表示使用过恢复结果。完整新鲜发布自举/PNG 不传 --resume-from。
没有阶段收据不能恢复，任何运行时/编译器/测试输入变化会使旧门禁整体失效。
这些输出是用户指定的运行数据，项目文档不保留历史副本。

## 并行调度

related 和 merge 均支持 --workers。独立测试使用有限进程，编译器代际按依赖顺序，性能阶段等回归进程退出后运行。

```sh
.venv/bin/python scripts/verify_preview.py merge --workers 2 \
  --timings-from /tmp/pixel-merge-PREVIOUS --output /tmp/pixel-merge-NEW
```

旧测试耗时只平衡分片，不跳过测试、不复用通过状态。比较进程数时保持测试集和调度耗时来源一致，
同时记录时间和内存。各子进程峰值之和不代表同时总内存。--resume-from 是独立显式机制。
