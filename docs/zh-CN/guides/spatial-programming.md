# 三维空间编程

[简体中文](spatial-programming.md) · [English](../../en/guides/spatial-programming.md) · [文档首页](../README.md)

本教程创建带类型化连接的三维空间，移动实体并比较旧、新快照，最后导出和恢复程序。先完成安装和第一个程序。

## 编写程序

将下面的程序保存为 `main.pxl`。X、Y、Z 都是空间坐标；时间独立于空间轴。

```pixellang
frame World = meters
record Road { cost: int, enabled: bool }

fn Count(network: Space[World, string, Road]) -> int {
    let origin = network.get(0)
    return len(network.neighbors(origin, 2.0))
}
flow Inspect() step(network: Space[World, string, Road]) -> int {
    let count = Count(network)
    output count
}
effect fn main() {
    var network = Space::[World, string, Road]("room")
    network = network.insert(Point3::[World]{x: 0.0, y: 0.0, z: 0.0}, "observer")
    network = network.insert(Point3::[World]{x: 0.0, y: 0.0, z: 1.5}, "moving")
    network = network.connect(network.get(0), network.get(1), Road{cost: 7, enabled: true})
    let before = network
    network = network.move(network.get(1), Point3::[World]{x: 0.0, y: 0.0, z: 3.0})
    print(Inspect().step(before))
    print(Inspect().step(network))
    let road = network.outgoing(network.get(0))[0]
    print(road.payload.cost)
    print(network.target(road).position.z)
}
```

`frame` 给坐标命名并声明长度单位。`Space[World, string, Road]` 分别指定坐标系、
实体负载类型和连接负载类型。实体具有稳定身份，移动只产生新空间快照；
`before` 仍保留旧位置。显式连接跟随端点身份，移动实体不会删除连接。

`fn` 封装纯计算，`flow` 通过表达式及命名结果表达计算依赖。
`Count(network)` 产生计算结果；`network.connect(a, b, payload)` 创建空间实体之间的关系。
Studio 图中的计算布局不会改变实体的空间距离。

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
