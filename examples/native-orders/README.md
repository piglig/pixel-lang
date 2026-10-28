# 原生订单状态机

[简体中文](README.md) · [English](README.en.md) · [文档](../../docs/zh-CN/README.md)

本示例在当前工具链中运行。先完成[安装](../../docs/zh-CN/guides/installation.md)；以下命令从仓库根执行。

订单使用类型化事件、枚举状态、纯函数和 `flow` 状态单元。转换函数直接用 `match (previous.status, event)` 表达状态与事件的组合；守卫检查金额、期限和运单号，最后一项显式拒绝非法转换。所有转换都在 PixelVM 中执行。

## 运行与回放

```sh
pixel application examples/native-orders/main.pxl --events examples/native-orders/events.json --steps 5 --record /tmp/orders.pxevents
pixel application examples/native-orders/main.pxl --replay /tmp/orders.pxevents --steps 0
```

## 事件与业务结果

顺序为 Tick 0、Tick 1、付款、Tick 2、发货。付款必须匹配 1299 分；发货需要已付款且运单号非空；未付款订单在逻辑时间 3 秒过期。业务拒绝返回类型化 `Err`，订单状态不变，但事件被消费并进入历史，不会阻塞后续输入。运行时故障才回滚整个步骤并保留待处理事件。

VS Code 中执行 **PixelLang: Open Native Application**，选择 `main.pxl`。先逐步初始化，再选择事件并填写字段。可以定位历史、保存回放，并通过 **Debug event** 检查选中转换的源码。

输出为 `Decision{order, outcome}`：`order` 是本步后的订单，`outcome` 为
`Result[Status, Rejection]`。拒绝包括 `AmountMismatch(expected, actual)`、
`PaymentExpired`、`InvalidTransition(status, event)` 和 `MissingTracking`。
调用者可以穷尽匹配错误类型，无需解析消息。Tick 也产生成功结果，因此每条历史事件均有对应输出。

该示例仅模拟订单状态，不执行真实扣款或发货；外部操作需要独立的命令执行、幂等与结果回传协议。

订单初值由 `Order(initial)` 传入，作为创建时配置。`Order` 保存 id、金额和期限，状态变化保持这些字段。`pixel run examples/native-orders/instances.pxl` 演示金额与期限不同的 A、B 两个订单；对 B 误用 A 的付款金额会被拒绝，且不影响 A。
