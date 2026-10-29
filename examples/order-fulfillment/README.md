# 订单履约

[简体中文](README.md) · [English](README.en.md) · [文档](../../docs/zh-CN/README.md)

跟随两笔订单完成付款、库存预留和发货。订单与仓库是同一不可变空间中的实体，带类型的连接记录预留数量。一批命令全部成功才发布最终快照；任何命令被拒绝，都保留原快照。

完成[安装](../../docs/zh-CN/guides/installation.md)后，在仓库根目录运行：

```sh
pixel run examples/order-fulfillment
pixel test examples/order-fulfillment
```

## 跟随订单变化

仓库起始有三件商品，每笔订单需要两件。

| 提交操作 | 结果 | 可用 / 预留库存 |
| --- | --- | --- |
| 创建 A-100 和 B-200 | 两笔订单待付款 | 3 / 0 |
| 一批支付两笔订单 | 库存不足，两笔付款均不生效 | 3 / 0 |
| 支付 A-100 | 已付款，建立预留连接 | 1 / 2 |
| 发货但不填写运单号 | 拒绝，保留快照 | 1 / 2 |
| 填写运单号并发货 | 已发货，移除预留连接 | 1 / 0 |
| 取消已发货的 A-100 | 拒绝，保留快照 | 1 / 0 |

随后读取之前保存的付款快照：A-100 仍是已付款状态，预留连接仍然存在。发货将当前订单移到物理位置 `(10, 0, 2)` 米；坐标表达位置，订单载荷表达业务状态。

取消已付款订单会释放库存；取消待付款订单不调整库存。测试还覆盖重复订单、重复付款、非法数量、独立流程实例和批次执行到一半被拒绝的情况。

## 录制与回放

```sh
pixel application examples/order-fulfillment/application.pxl --events examples/order-fulfillment/events.json --steps 6 --record /tmp/fulfillment.pxevents
pixel application examples/order-fulfillment/application.pxl --replay /tmp/fulfillment.pxevents --steps 0
```

在 VS Code 中执行 **PixelLang: Open Native Application**，选择 `application.pxl` 使用事件应用。每个 `Submit` 携带一组命令。业务拒绝会消费并记录事件，同时保留原空间快照；执行故障则回滚本步，并保留待处理事件。

## 阅读实现

- [model.pxl](model.pxl)：订单、命令、结果和带类型的预留数据。
- [operations.pxl](operations.pxl)：校验状态迁移、查询库存和批量处理。
- [workflow.pxl](workflow.pxl)：成功时发布新快照，拒绝时保留旧快照。
- [main.pxl](main.pxl)：可运行的操作过程及历史快照读取。
- [tests/fulfillment_test.pxl](tests/fulfillment_test.pxl)：业务约束和拒绝场景。

订单业务键长度为 1–244 个字符。内部前缀将订单实体、预留连接和仓库分开。类型化标识归属空间，同一空间的快照保留相同标识。`Open` 要求起始库存非负。`Find` 返回可选订单；`Available` 和 `Reserved` 查询库存，后者通过仓库的出向连接汇总预留数量。

这里的批量原子性是只发布最终不可变值，尚未提供优化后的存储事务；中间更新仍会复制空间数据。示例只模拟付款和发货，不执行外部操作；真实对接需要独立的幂等与投递协议。

## 测量开发成本

```sh
python scripts/benchmark_order_fulfillment.py --sizes 50 150 450 --trials 3 --output /tmp/fulfillment-benchmark.json
```

基准分别记录单个函数体修改的编译指标，以及不同批次规模的 VM 执行耗时，并保存源码与编译器哈希。空间分配计数是计量单位，不是保留内存或进程字节数。对比测量时请保持电源与机器条件一致。

Python 回归测试还覆盖 PNG 导出恢复与录制历史查询：

```sh
python -m unittest tests.test_order_fulfillment -q
```
