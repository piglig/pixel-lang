# 三维空间编程

[简体中文](spatial-programming.md) · [English](../../en/guides/spatial-programming.md) · [文档首页](../README.md)

一个对象移远之后，程序会得到什么不同的结果？本教程会创建两个实体，沿 Z 轴移动其中一个，再观察“查找附近实体”的结果如何变化。你还会连接这两个实体，并把程序导出成 PNG。如果你还没有运行过 PixelLang，请先完成[第一个程序](../learn/getting-started.md)。

## 编写程序

将下面的程序保存为 `main.pxl`。X、Y、Z 都是空间坐标；时间独立于空间轴。

```pixellang
frame World = meters
record Road { cost: int, enabled: bool }

fn Count(network: Space[World, string, Road]) -> int {
    let origin = network.entityId("observer")
    return len(network.neighbors(origin, 2.0))
}
flow Inspect() step(network: Space[World, string, Road]) -> int {
    let count = Count(network)
    output count
}
effect fn main() {
    var network = Space::[World, string, Road]("room")
    network = network.insert("observer", Point3::[World]{x: 0.0, y: 0.0, z: 0.0}, "observer")
    network = network.insert("moving", Point3::[World]{x: 0.0, y: 0.0, z: 1.5}, "moving")
    network = network.connect("road", network.entityId("observer"), network.entityId("moving"), Road{cost: 7, enabled: true})
    let before = network
    network = network.move(network.entityId("moving"), Point3::[World]{x: 0.0, y: 0.0, z: 3.0})
    print(Inspect().step(before))
    print(Inspect().step(network))
    let road = network.outgoing(network.entityId("observer"))[0]
    print(road.payload.cost)
    print(network.require(road.target).position.z)
}
```

观察者留在原点，另一个实体最初距离它 1.5 米，在 2 米的搜索范围内。移动到 Z = 3 后，它就超出了这个范围。两次 `Inspect().step(...)` 分别查询移动前后的空间，让你看到结果的变化。

可以留意这几个部分：

- `frame World = meters` 为坐标系命名，并指定以米为单位。
- `Space[World, string, Road]` 中的实体带有字符串标签，连接带有 `Road` 数据。
- `before` 保留移动前的空间，因此仍能查询原来的位置。
- 实体移动后，道路仍连接着同样的两个实体，成本也仍为 `7`。

`Count` 是计算结果的普通函数，`Inspect` 则在每一步产生输出。在 Studio 中移动图上的框，只会改变视图；调用 `network.move(...)`，才会改变程序使用的空间。

## 运行、交付和恢复

```sh
pixel run main.pxl --json
pixel pack main.pxl -o room.png
pixel run room.png --json
pixel unpack room.png -o restored --json
pixel run restored/main.pxl --json
```

三次 `run` 都应返回 `output: [1, 0, 7, 3.0]`：旧空间有一个邻居，
移动后没有；连接权重仍为 7，在新快照解析连接端点得到 Z=3。
`pack` 保存可恢复的原生程序语义；PNG 不只是截图。恢复目标目录使用新的路径。

将恢复源码中 `cost: 7` 改为 `cost: 9`，再次运行，第三项输出应变为 9。
坐标、连接负载及其类型均保留在可编辑程序中。

## 在 Studio 中查看

在 VS Code 打开源码，执行 **PixelLang: Open Native Graph**。在 `Count` 的返回
语句设置断点并启动 PixelLang 调试。暂停后，原生图检查器显示当前 flow 输入；
点击空间值的 **Space** 按钮查看实体及有向连接。

选择实体后，**Orbit** 只旋转相机；**Move X/Y/Z** 拖动可以写回有直接源码来源的
位置字面量。拖动后旧调试视图失效，需要重启调试运行修改后的程序。聚焦源码
编辑器后，使用普通撤销快捷键恢复修改。计算得到的位置不能直接拖动覆盖；
使用 **Reveal expression** 定位到产生该位置的运算或坐标变换表达式。

## 进一步使用

- 不同坐标系不可隐式混用。位置用 `Point3[F]`，位移用 `Vec3[F]`；变换使用 `Transform3[From, To]`。
- 连接负载支持记录和容器。取出的容器采用值语义；后续本地修改不会改变原始快照和历史数据。
- 状态化 flow 使用 `state`、`next`、显式构造和 `instance.step(...)`，见[原生数据流](../internals/dataflow.md)。
- [带权路径示例](../../../examples/weighted-routes/README.md)展示连接状态与权重如何决定跨步骤传播结果。
- 完整操作契约见[原生空间模型](../reference/language/spatial.md#原生空间接口)。

- [原生电梯示例](../../../examples/native-elevator/README.md)将多文件函数、有状态 flow、三维邻域和类型化停靠连接结合起来，包含失败步骤回滚与独立实例。

## 继续阅读

- [完整文档目录](../README.md)
- [安装工具链](installation.md)
