# 状态机与事件应用

[简体中文](state-machines.md) · [English](../../en/guides/state-machines.md) · [文档首页](../README.md)

订单需要记住是否已经付款，计数器需要记住当前的数值。`flow` 让程序能够保留这样的状态。本指南先从一个响应事件的计数器开始，再介绍如何把它作为应用运行，以及如何查看它的历史。


**本页内容**

- [只处理业务事件](#只处理业务事件)
- [注册应用](#注册应用)
- [逻辑时间和事件顺序](#逻辑时间和事件顺序)
- [运行与检查](#运行与检查)
- [逻辑时间标准库](#逻辑时间标准库)
- [记录与限制](#记录与限制)
- [创建时参数与每步输入](#创建时参数与每步输入)
- [继续阅读](#继续阅读)

## 只处理业务事件

```pixellang
enum Event { Add(amount: int), Reset }
flow Counter(initial: int) step(event: Event) -> int {
    state count = initial
    let updated = match event { .Add(amount) => count + amount, .Reset => initial }
    next count = updated
    output updated
}
effect fn main() { Counter(10) }
```

创建参数是只读配置，可在每步计算中直接使用。宿主按入队顺序处理业务事件；
只有实际事件才产生一次状态转换。队列为空时不能继续执行，Studio 会停止播放。
首次输出来自第一个事件，不注入隐藏的初始化事件。录制、历史定位、调试和日志恢复均可使用。

单输入应用只接受宿主 `instance` 选择，不接受 `tick`、`intervalNanoseconds` 或未来时间调度。
CLI 事件文件中写 `[{"input":{"variant":"Add","fields":{"amount":5}}}]`，无需提供 tick。
需要定时事件时，显式采用下方的双输入时钟协议。

## 注册应用

```pixellang
import "std/time.pxl" as time

enum Event { Tick, Add(value: int) }
fn Update(previous: int, event: Event) -> int {
    match event {
        Event.Tick => { return previous },
        Event.Add(value) => { return previous + value }
    }
}
flow Counter() step(event: Event, clock: time.Step) -> int {
    state total: int = 0
    let updated = Update(total, event)
    next total = updated
    output updated
}
effect fn main() { Counter() }
```

应用在 main 中创建 flow，main 正常结束后由宿主选择实例并调度。默认要求恰好一个根实例，使用 Tick 事件和 100 毫秒逻辑步长。CLI 的 `--config application.json` 或 Studio 的 `pixellang.application` 设置可指定 `instance`、`tick` 和 `intervalNanoseconds`；这些属于宿主配置，不属于语言语法。应用 flow 按顺序接收枚举事件和时钟记录。步长必须是正整数纳秒。

时钟协议要求 `index: int`、`elapsed: Duration`、`delta: Duration`，Duration 记录仅包含 `nanoseconds: int`。记录在语言内部仍按声明区分类型；应用边界校验字段协议，不依赖可选模块名称。因此不保留原始名称的可执行 PNG 也能恢复应用。

## 逻辑时间和事件顺序

- 第一个转换是 Tick 0，elapsed 和 delta 都为零。
- Tick N 的 elapsed 为 `N × interval`；N > 0 时 delta 为 interval。
- 同一 Tick 的输入在 Tick 事件之后按入队顺序执行，delta 为零。多个输入不会额外推进时间。
- 未指定 Tick 的输入进入当前 Tick；启动前进入 Tick 0。
- 每个正常返回的事件只提交一次。执行故障会回滚虚拟机与 flow 状态，待处理输入保留，供重试或显式丢弃。业务 `Result.Err` 是正常返回值，仍被消费和记录；是否保留业务状态由转移函数显式决定。
- 定位历史会重建原生状态。新增输入或推进实时运行前，先返回 **Latest**。从历史位置播放会先重放已有事件。

XYZ 表达空间坐标，时间是独立逻辑值。屏幕刷新率和播放倍率不改变程序输入。整数纳秒避免时间累积漂移；时长换算和时钟溢出均有检查。

## 运行与检查

```sh
pixel application examples/native-orders/main.pxl --events examples/native-orders/events.json --steps 5 --record /tmp/orders.pxevents
pixel application examples/native-orders/main.pxl --replay /tmp/orders.pxevents --steps 0
```

`--steps` 统计包含输入在内的事件转换次数。`--seek N` 检查第 N 个事件之后的状态；0 表示应用已注册但尚未执行事件。`--replay` 恢复记录、返回最新事件，再执行指定数量的额外步骤。只检查记录时使用 `--steps 0`。

CLI 默认只执行应用并保留最新输出，不积累事件历史；例如 `--steps 10000` 不受调试记录的 4,096 事件上限约束。`--history` 显式启用历史，`--record`、`--replay` 或 `--seek` 也会自动启用。未记录的执行不能事后恢复逐事件历史。两种模式共用事件调度、时钟与原子提交实现。

入口也可以是可执行 PNG；沿用现有 PNG 导出和恢复命令，不附带另一份隐藏字节码。

在 VS Code 执行 **PixelLang: Open Native Application**，选择 `.pxl` 入口或 PNG。面板提供播放、暂停、逐步、历史定位、类型化事件选择、状态检查和原生三维视图。字段文本由虚拟机按事件枚举校验，64 位整数经过编辑器边界仍保持精确。复杂字段可参考面板中的记录与枚举结构。待处理输入可显式丢弃。

**Debug event** 在独立虚拟机中重建选中的已完成事件，打开现有源码调试器，支持反向单步及原生 flow 检查，不改变实时应用。保存应用记录即可在以后重现这些调试会话。

## 逻辑时间标准库

`std/time.pxl` 导出 `Duration`、`Step`、`Nanoseconds`、`Milliseconds`、`Seconds`、`InSeconds`、`At`。`At(0, interval)` 的 delta 为零。

## 记录与限制

### 持久化应用日志

```sh
pixel application examples/native-orders/main.pxl --journal /tmp/orders.pxjournal --events examples/native-orders/events.json --steps 5
pixel application examples/native-orders/main.pxl --journal /tmp/orders.pxjournal --steps 10000
pixel application examples/native-orders/main.pxl --journal /tmp/orders.pxjournal --steps 0 --seek 3
```

`--journal` 创建或继续同一个 SQLite 事务日志。输入入队、显式丢弃和成功事件分别在事务提交后返回成功；恢复保留尚未处理的输入及其顺序。续跑时无需再次传入已提交的 `--events` 文件，再次传入会再次投递这些输入。

恢复校验完整的日志摘要链，从最近的有效检查点恢复类型化状态，再执行后续命令并核对事件顺序与输出摘要。日志绑定编译产物、步长、启动输入与运行预算，不保存可执行 VM 快照。事务未提交时进程退出不会发布半条命令；提交结果不确定或另一个会话先写入时，当前会话停止写入，重新打开日志后以已提交记录为准。

历史描述按页从磁盘读取，每页最多 128 个事件；`--seek` 在独立 VM 中重建指定事件之后的状态，不改变持久化运行的最新位置。内存不随事件总数保存全部历史，默认日志载荷上限为 1 GB（数据库实际文件还包含索引和事务开销）。日志模式没有 4,096 事件上限。

在 VS Code 执行 **PixelLang: Open Native Application Journal**，选择创建新日志或恢复已有日志，再打开对应的源码或 PNG。面板沿用播放、逐步、历史定位和类型化输入；**Debug event** 从日志重建选中事件，在独立 VM 中进行源码单步与反向调试。日志自动保存已提交操作，无需导出内存记录。关闭面板释放会话，再次打开从日志的最新提交位置继续。

每 128 个成功事件尝试在同一事务中保存一次状态检查点，最多保留 8 个，总载荷不超过 64 MB。检查点保存 flow 状态、空间与连接身份、逻辑时钟、待处理输入和最近输出；不可变共享值只编码一次。临时 VM 堆和指令位置不保存，恢复时由原程序重新注册执行入口。检查点超出编码或存储预算时跳过它，事件仍正常提交并可从更早位置重放。

日志模式不能与 `--history`、`--record`、`--replay` 混用。历史检查另保留最多 64 MB 的进程内前缀缓存，向前查看只执行新增命令；首次查看、向后定位、单事件调试与重启恢复可使用磁盘检查点。完整性校验仍会读取对应日志前缀，因此恢复成本还包含日志读取与摘要计算。当前日志格式版本为 2，不兼容此前开发阶段的日志格式。日志运行要求新建且未授权外部文件访问的 VM，外部业务副作用的持久化协议另行实现。

### 内存调试记录

`.pxevents` 记录绑定精确的编译产物和步长。恢复时重新执行类型化输入并核对每个输出，不反序列化可执行状态。修改程序或使用不同编译产物时，需要新建记录。记录还保存当前历史位置及待处理输入。

应用执行默认限制为 1,024 个待处理输入、64 MB 待处理输入预算、每事件一百万条指令和 64 MB 单次输出预算，另受 VM 堆与逻辑时间范围约束。执行模式不保存过去事件，仅保留最新输出和当前状态。

每个成功事件结束后，原生分配计数按 VM 仍引用的空间值和 flow 状态重新计算，共享快照只计一次。已丢弃的临时值不会永久累积计费；单次事件的临时分配和持续增长的有效状态仍受预算限制。队列、输出与历史分别使用宿主预算。

Studio 和启用内存历史的 CLI 会话额外限制为 4,096 个已完成事件、64 MB 历史字节预算和独立的 64 MB 检查点预算。旧检查点可能被淘汰，历史仍可通过确定性重放访问。记录达到限制时停止推进并报错，不会静默丢失记录。持续执行与磁盘日志使用上面的独立模式；当前不提供外部业务事务或实时渲染引擎。

参见[订单示例](../../../examples/native-orders/README.md)和[原生空间语言参考](../reference/language/spatial.md)。

## 创建时参数与每步输入

`flow Counter(start: int) step(delta: int) -> int` 的第一组参数只在创建时传入，第二组是每步输入。
没有创建参数时也须在 `step(...)` 前写 `()`。创建参数是实例持有的只读配置，在状态初值、子实例参数和每步表达式中均可用。
创建时按值保存，调用者后续修改不会改变配置；闭包按值捕获配置。只有会随步骤变化的值才需要 state。

```pixellang
flow Counter(start: int) step(delta: int) -> int {
    state value: int = start
    let updated = Add(value, delta)
    next value = updated
    output updated
}
fn Add(previous: int, delta: int) -> int = previous + delta
effect fn main() {
    let first = Counter(10)
    let second = Counter(100)
    print(first.step(1))
    print(second.step(1))
}
```

输出 11 和 101。参数按从左到右顺序求值一次，实例保存独立初值。
在父 flow 中写 `instance child = Counter(start + 1)`，即可用父实例的创建参数配置子实例。
子实例参数不依赖每步输入、其他状态或每步计算结果，防止初始化顺序决定程序行为。

初值允许构造记录、枚举、容器与空间值，使用创建参数的字段、下标及算术表达式；
不允许任意函数调用、I/O 或隐式时钟。较复杂的纯计算可以在构造实例前显式完成。
常量表达式仍在编译期求值；依赖参数的计算在创建时执行，错误时不发布部分实例。
创建参数遵循声明类型与状态载荷限制。普通记录与容器采用值语义；之后修改本地构建数据不会改变已创建实例的初值。

应用宿主只约束每步签名为事件与时钟，创建参数不参与该签名。
订单示例现在通过 `Order(initial)` 传入订单身份、金额和期限；
运行 `pixel run examples/native-orders/instances.pxl` 可查看两个不同配置的独立订单。

### 在 Studio 中编辑创建参数

原生图面板的 **Add child instance** 会根据目标 flow 的创建签名显示参数输入框。
每个框填写一个 PixelLang 表达式；创建参数与每步输入分开显示，子实例通过 `child.step(...)` 显式推进。
选择已有子实例，在 **Creation parameters** 中修改参数并点击 **Update creation parameters**，
即可写回源码；编辑器中的一次撤销可以恢复该次修改。修改声明会影响之后新建的实例，
不会重新初始化正在运行的实例，需要重启运行观察变化。

写回前使用原生编译器检查表达式、类型和初始化限制。flow 结构尚未写完整不阻止参数检查；
其他绑定或类型错误可能需要先修复。已有表达式按原生 AST 范围修改，保留表达式范围外的
注释和换行。若现有参数数量错误且已有参数列表，请先在文本中修复数量。

## 继续阅读

- [完整文档目录](../README.md)
- [安装工具链](installation.md)
