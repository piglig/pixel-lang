# 性能测量

[简体中文](performance.md) · [English](../../en/contributing/performance.md) · [文档首页](../README.md)

先固定工作负载，再比较冷启动、热请求和修改后构建。计时、指令和内存分别报告，避免把验证耗时当成编辑延迟。


**本页内容**

- [规则与接口](#规则与接口)
- [命令](#命令)
- [缓存与并行](#缓存与并行)
- [原生应用恢复](#原生应用恢复)
- [不重建编译器的运行时剖析](#不重建编译器的运行时剖析)
- [普通值更新](#普通值更新)
- [单次构建与会话缓存](#单次构建与会话缓存)
- [原生编译器热路径](#原生编译器热路径)
- [常驻函数检查与 IR 缓存](#常驻函数检查与-ir-缓存)
- [继续阅读](#继续阅读)

## 规则与接口

分别记录编辑延迟、单次编译、PNG 导出/恢复和完整验证。小项目数据不代表完整编译器。
固定源码、编译器、运行时、输入、选项和电源，计时任务顺序运行，不相互竞争 CPU。
测量期间保持系统唤醒；仅接通电源不代表系统不会闲置睡眠。macOS 可在命令前加 `caffeinate -i`，
防止该命令执行期间的闲置睡眠，命令退出后自动解除。
输出写到新的仓库外目录，文档不保留历史测量。

## 命令

```sh
.venv/bin/python scripts/benchmark_service.py --output /tmp/pixel-service-NEW.json
.venv/bin/python scripts/benchmark_studio.py --project examples/ledger --output /tmp/pixel-studio-NEW.json
.venv/bin/python scripts/benchmark_studio_bundle.py --output /tmp/pixel-bundle-NEW.json
.venv/bin/python scripts/benchmark_incremental_ir.py --native-compiler pixellang/artifacts/compiler-native --trials 3 --output /tmp/pixel-ir-cache-NEW
.venv/bin/python scripts/profile_selfhost.py --functions 100 --output /tmp/pixel-profile-NEW
.venv/bin/python scripts/profile_image.py --compiler pixellang/artifacts/compiler.json --self --timeout 1800 --output /tmp/pixel-image-full-NEW
.venv/bin/python scripts/benchmark_png_pipeline.py --output /tmp/pixel-png-pipeline-NEW
```

记录墙钟、VM 指令、峰值内存、冷启动/热缓存和产物摘要。带 profiler 的计时用于定位，正式加速对照不带探针。
完整 PNG 命令加 --plain 关闭探针，记录电源、原/恢复编译器摘要和 RSS，恢复编译器需运行新程序。

`benchmark_png_pipeline.py` 在同一编译服务中依次测量完整编译器的 PNG 导出、从 PNG 编译和源码恢复，
关闭解析、IR 与产物缓存，核对恢复编译器能编译新程序。`--functions 10` 可快速检查测量工具，
但不能代替完整规模验收。比较两份冻结代码时，用 `--workload sources.json` 向两组提供同一份
编译器源码映射，逐项核对产物摘要，并保留两个输出目录。
`--compiler compiler.json --native-compiler compiler-native` 可验证匹配的候选产物，无需替换随附产物。
三项计时均使用原生编译器，校验可执行文件、字节码和源码映射的绑定；小负载的执行正确性检查使用参考 VM，完整编译器的冒烟检查在计时外运行。输出目录保存
`program.png`、恢复出的 `compiler.json` 和源码 `restored.json`，可直接用于后续回归，避免重复解码。
每阶段记录的 RSS 仅为宿主 Python 进程启动以来的累计峰值，不含原生编译器子进程，
不能当作整条流水线内存。它也不是该阶段独立峰值；宿主总峰值还包含结果校验。
`wallSeconds` 使用单调性能时钟；某些系统上它不包含休眠。`clockElapsedSeconds` 同时记录
实际钟表时间，`clockGapSeconds` 给出两者差值。休眠或系统校时都会影响差值，不能将其直接当作 CPU 时间。
堆对象数和容器大小用于定位，不能替代 RSS：临时副本、输出对象与 Python 分配器也占用内存。

## 缓存与并行

`benchmark_studio.py --edited-project /path/to/edited-copy` 在首次构建和相同输入
复用之后，测量真实源码修改与修改后的重复构建。第二个目录必须包含相同入口且内容
有所变化；报告记录改动路径和两份源码摘要，并验证修改后首次构建没有命中精确产物
缓存、重复构建才命中。排查编辑延迟时可加 `--interactive-only`，省略投影、PNG 和
录像恢复；此模式通过不代表这些交付路径通过验收。首次、修改后和未修改的耗时必须
分别报告，首次构建还包含编译器工作进程的启动成本。

PixelLang 输出通道记录每个已完成 Studio 请求的总耗时，以及在扩展传输队列中的
等待时间。其余时间包含服务端等待与执行，不能视为编译各阶段的独立耗时。
`benchmark_native_compile.py` 报告另行记录 `serviceStartupSeconds`，将编译器映像
加载与编译执行样本分开。

Studio 对照交替独立请求与共享事务，核对全部产物。精确产物缓存和解析缓存是不同层。
常驻缓存对照交替开关 incremental，两组均设 reuseArtifacts: false 并执行相同的预热请求；同时记录首次构建和修改后成本，
比较产物摘要及实际运行结果。命中数量不等于提速，初次填充和传输可能增加成本。

验证可用 --timings-from 平衡工作，测试仍全部执行；--resume-from 才复用精确成功阶段。
按同一测试集比较 1、2、4 进程和内存，性能门禁在回归退出后独占运行。见[分层验证](verification.md)。
源码或机器变化需重新测量，不将某次比例作为性能保证。原生编译器与 Python VM 的结果应分别标明，不能将不同执行路径的耗时混为同一基准。

PNG 编译统一使用随附原生编译服务，不再构建独立图片工作器或维护第二份磁盘解析缓存。
端到端测量使用 `benchmark_png_pipeline.py`，完整编译器交付使用原生自举与源码恢复门禁。
分块传输验证保留在 `test_image_transport.py`。

## 原生应用恢复

```sh
.venv/bin/python scripts/benchmark_native_recovery.py --output /tmp/pixel-recovery-NEW --events 1000 --samples 3
```

默认使用原生订单示例，也可通过 `--source` 指定入口。分别构造完整重放与持久化检查点日志，
每个样本使用独立进程和同一原始日志的副本，核对恢复状态与下一事件输出。报告记录恢复时间、
含进程启动的总时间、日志大小、源码指纹与测量前后电源设置；编译和日志构造不计入恢复时间。
在其他验证进程退出后运行，输出目录必须位于仓库外且尚不存在。

## 不重建编译器的运行时剖析

```sh
.venv/bin/python scripts/benchmark_native_compile.py examples/native-orders --repeat 5 --profile --output /tmp/pixel-orders-profile
```

使用附带原生编译器，禁用增量缓存，重复编译指定目录中的源码；不会自编译或生成 PNG。
每轮创建并关闭独立服务，避免混合首次初始化与已有会话的指令数。
`serviceStartupSeconds` 记录宿主服务构造时间；原生进程的启动和首次协议初始化仍计入请求耗时。
输出目录必须不存在。`results.json` 保存各轮耗时、指令数、产物摘要、编译器清单和宿主源码摘要；
`sources.json` 保存输入快照。`--profile` 额外执行一轮 cProfile，保存 `runtime.prof`，
该轮不参与耗时中位数。各轮必须产生相同字节码与指令数。
测量范围不含服务启动、PNG 编解码和程序运行，电源条件没有自动控制；
局部函数的剖析改善不能直接解释为完整自编译或交付链路提速。

`profile_selfhost.py` 在 Python 插桩 VM 中执行编译器字节码，不重建驱动，也不调用
Python 编译前端。可用 `--compiler` 选择冻结字节码；默认读取并校验附带产物。
`phases.json` 按 VM 调用边界记录阶段独占耗时与指令数，辅助函数归入最近的已标记调用方。
`profile_image.py` 同样在参考 VM 中执行，`--plain` 仅关闭插桩，不切换到原生执行。
这些探针用于定位阶段工作量，不能用其耗时代表原生编译或 Studio 延迟。

旧的 `benchmark_selfhost.py` 已移除。合成函数负载统一使用原生编译基准：

```sh
.venv/bin/python scripts/benchmark_native_compile.py --functions 100 --repeat 3 --output /tmp/pixel-functions-100-NEW
.venv/bin/python scripts/benchmark_native_compile.py --functions 200 --repeat 3 --output /tmp/pixel-functions-200-NEW
```

每个函数均可达，计时后检查全部输出；比较两组指令数与耗时，不沿用旧 VM 的硬编码阈值。
该入口也支持成对的 `--compiler`／`--native-compiler`，记录产物摘要和电源状态，失败时关闭服务。
PNG 编解码正确性由编解码测试覆盖，交付性能统一使用上面的 PNG 流水线基准。

## 普通值更新

`python scripts/benchmark_value_updates.py --output /tmp/value-updates.json` 分别测量数组追加、
数组下标写入、映射插入与空间流程状态推进。源码只编译一次；运行计时不包含编译和 VM 初始化。
空间流程逐步插入实体并保留最初快照；基准验证输出、数组原值与空间快照隔离，并记录源码、编译器和运行时指纹、电源状态、
各规模用时与 VM 堆分配数量。该数量不包含原生空间存储，不能视为进程总内存。`--compiler` 与 `--native-compiler` 分别指定匹配的编译器字节码与原生可执行文件，
启动时校验其映射与摘要；`--profile` 保存剖析数据。

发布对照应在接通电源、机器空闲且相同电源设置下运行，不与自编译同时测量。
剖析会改变用时，适合定位重复工作，不应作为最终速度成绩。
当前值更新复用已验证条目的类型与映射键信息，只检查改变的数据；数组更新使用 radix-32 结构共享存储，仅复制改变路径；映射使用哈希前缀树查找键，
并用结构共享数组保存插入顺序。删除条目积累后会压缩顺序表。纯标量映射无需追踪
堆引用，含引用数据的垃圾回收成本仍需进一步优化。任何优化都必须保留别名、历史状态、
分配失败与取消时的隔离语义。

## 单次构建与会话缓存

单次驱动构建使用 `incremental=False`，避免建立无法复用的会话快照。内容寻址的测试驱动产物缓存与编译会话缓存是不同层。
开发候选统一在常驻进程中复用解析、函数检查和 IR，无语义／IR 文件序列化。
必须明确报告测量所用的编译器字节码与原生可执行文件指纹。
对照使用相同输入，关闭 reuseArtifacts，交替开关 incremental，并核对字节码一致。

`benchmark_studio.py` 分别测量交互冷／热构建、按需空间投影、PNG 生成和录像恢复，并断言普通构建与恢复不生成像素产物、相同输入缓存命中执行零条编译指令、查看产物不改变录像。项目耗时没有人为放宽的阈值；原小型服务性能限额仍单独验收。
该测量覆盖工作站服务，不包含 VS Code 渲染及前端收集／传输。源码不变的构建不得请求编译器，
只能共享不可变代码映像，每次构建仍须创建独立 VM。

空间状态基准在一次程序执行内连续推进，保留现有空间分配预算。报告中的
`completed: false` 和 `budget-exceeded` 表示触及预算，`medianSeconds` 留空；
`verified` 只确认测量与输入校验有效，不代表所有规模均完成。应用的事件边界会重新核算存活空间状态，
因此不能将该单次执行上限直接当作应用可持有的实体数量上限。

## 原生编译器热路径

原生运行时仅在函数退出确实释放借用时清理访问记录；清理按祖先到后代传播失效，
再按后代到祖先释放，避免反复全表扫描，同时保留独占访问与生命周期检查。
编译服务通过 `--buffered-output` 合并标准输出写入，成功报告前检查最终刷新结果。
普通原生程序默认仍逐次刷新输出；缓冲模式不改变输出预算、取消和截止时间限制。

修改单个文件的剖析应分开观察解析缓存暂存、全项目语义分析、字节码生成、
JSON 传输及调试映像构造。解析命中率高不能证明语义分析是增量的；多线程解析
也不能消除已经命中缓存之后的全项目工作。优先依据依赖关系减少重复计算，再评估并行。

## 常驻函数检查与 IR 缓存

`incremental` 控制原生会话复用。绑定与声明类型有独立的声明查询计数；
`semanticCacheHits` 和 `semanticCheckedFunctions` 统计顶层函数与 flow 查询，
闭包归属于外层查询。flow 的 IR、初始化函数与图元数据整体复用。帧规划也有原生查询，
但其计数目前尚未通过服务公开。

这些计数不覆盖全部工作：全局索引、约束分析和特化仍需进一步增量化。
`residentIRNodes` 是结构计数，不是字节数。查询依赖、失效、失败处理和容量规则统一见
[编译服务参考](../reference/compiler-service.md)，不能仅凭命中数宣称完整增量编译已完成。

使用匹配的候选编译器进行对照：

```sh
.venv/bin/python scripts/benchmark_semantic_incremental.py \
  --compiler /path/to/compiler.json --native-compiler /path/to/compiler-native \
  --output /tmp/semantic-benchmark.json
```

脚本修改编译器的一个函数，同时对比关闭和开启函数缓存的构建，核对字节码完全一致。
验收需要同时报告缓存命中、重算数量和端到端耗时；即使命中率很高，如果缓存序列化、
恢复和依赖核验使总耗时增加，也不能默认启用或宣称性能目标完成。

## 继续阅读

- [完整文档目录](../README.md)
