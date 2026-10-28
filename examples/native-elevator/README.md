# 原生空间电梯

[简体中文](README.md) · [English](README.en.md) · [文档](../../docs/zh-CN/README.md)

本示例在当前工具链中运行。先完成[安装](../../docs/zh-CN/guides/installation.md)；以下命令从仓库根执行。

四层电梯直接使用 PixelLang 的 `fn`、`flow` 与 `Space`。控制器状态和
三维场景由同一 flow 步骤原子提交；楼层间距为 3 米，每个 `Tick` 移动一层。

- `model.pxl` 声明坐标系、事件、控制器状态和输出类型。
- `controller.pxl` 实现状态转移、空间快照更新和几何邻域查询。
- `main.pxl` 用普通表达式组合三个纯函数，演示两个独立实例、失败事件和历史快照。

## 运行

在仓库根执行：

```sh
pixel run examples/native-elevator --json
pixel pack examples/native-elevator/main.pxl -o elevator.png
pixel run elevator.png --json
pixel unpack elevator.png -o elevator-restored --json
pixel run elevator-restored/main.pxl --json
```

每次成功事件输出三项：`[当前楼层, 目标楼层, 停靠连接数]`、是否开门、几何邻域
内的楼层身份。运行过程如下：

| 事件 | 当前／目标 | 门 | 邻近楼层 | 停靠连接 |
| --- | --- | --- | --- | --- |
| 请求 3 层 | 0／3 | 关闭 | `[0]` | 0 |
| 行程中开门 | 拒绝，状态不变 | 关闭 | — | — |
| Tick | 1／3 | 关闭 | `[1]` | 0 |
| Tick | 2／3 | 关闭 | `[2]` | 0 |
| Tick | 3／3 | 打开 | `[3]` | 1 |
| 请求 1 层 | 3／1 | 关闭 | `[3]` | 0 |
| Tick | 2／1 | 关闭 | `[2]` | 0 |
| Tick | 1／1 | 打开 | `[1]` | 1 |
| 第二个实例开门 | 0／0 | 打开 | `[0]` | 1 |

最后的 `9.0` 与 `1` 来自此前停在 3 层的快照，说明后续移动没有改写历史位置
或连接。非法开门事件输出 `rejected: Cannot open doors during a trip`，随后
的 Tick 仍从原状态继续。请求楼层的合法范围是 0–3。

流程返回 `Result[View, Rejection]`。`InvalidFloor(floor)`、`DoorsOpen` 和 `InTransit`
是类型化业务拒绝，调用者通过 `match` 处理。拒绝分支显式保留状态和场景，同时完成本次步骤；
执行故障仍由运行时中止整个步骤。首次输入无效时不会尝试读取尚未创建的轿厢实体。

## 三维语义与调试

楼层实体身份为 0–3，轿厢身份为 4；相同身份属于各自独立的空间实例。
Y 表示高度，Z 是空间坐标，逻辑步与三维坐标独立。`space.neighbors(...)` 使用 1.1 米
半径查找楼层；开门且邻近目标楼层时建立携带 bool 数据的有向停靠连接。

在 VS Code 打开 `main.pxl`，执行 **PixelLang: Open Native Graph**。在
`Inspect` 中设置断点并启动调试，可查看控制器、旧／待提交空间快照、实体和
停靠连接，并回看之前的步骤。

恢复源码后，若仅将 `Arrange` 中轿厢移动位置的 Z 字面量改为 `2.0`，邻近楼层
和停靠连接都会消失。这一变化由空间距离计算决定。恢复测试通过编译器给出的
源码范围定位坐标，保留计算得到的 Y 表达式。
