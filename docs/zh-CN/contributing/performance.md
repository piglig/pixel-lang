# 性能测量

[中文](performance.md) · [English](../../en/contributing/performance.md) · [文档首页](../README.md)

分别记录编辑延迟、单次编译、PNG 导出/恢复和完整验证。小项目数据不代表完整编译器。
固定源码、编译器、运行时、输入、选项和电源，计时任务顺序运行，不相互竞争 CPU。
输出写到新的仓库外目录，文档不保留历史测量。

## 命令

```sh
.venv/bin/python scripts/benchmark_service.py --output /tmp/pixel-service-NEW.json
.venv/bin/python scripts/benchmark_studio_bundle.py --output /tmp/pixel-bundle-NEW.json
.venv/bin/python scripts/benchmark_incremental_ir.py --trials 3 --output /tmp/pixel-ir-cache-NEW
.venv/bin/python scripts/profile_selfhost.py --functions 100 --output /tmp/pixel-profile-NEW
.venv/bin/python scripts/profile_image.py --compiler pixellang/artifacts/compiler.json --self --timeout 1800 --output /tmp/pixel-image-full-NEW
```

记录墙钟、VM 指令、峰值内存、冷启动/热缓存和产物摘要。带 profiler 的计时用于定位，正式加速对照不带探针。
完整 PNG 命令加 --plain 关闭探针，记录电源、原/恢复编译器摘要和 RSS，恢复编译器需运行新程序。

## 缓存与并行

Studio 对照交替独立请求与共享事务，核对全部产物。精确产物缓存和解析缓存是不同层。
IR 对照交替开关 reuseIR，两组均设 reuseArtifacts: false 并同样预热解析；同时记录首次构建和修改后成本，
比较产物摘要及实际运行结果。命中数量不等于提速，初次填充和传输可能增加成本。

验证可用 --timings-from 平衡工作，测试仍全部执行；--resume-from 才复用精确成功阶段。
按同一测试集比较 1、2、4 进程和内存，性能门禁在回归退出后独占运行。见[分层验证](verification.md)。
源码或机器变化需重新测量，不将某次比例作为性能保证。原生 VM 的边界与命令见[原型](../internals/native-vm.md)。
