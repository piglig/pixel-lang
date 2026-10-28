# 空间与源码布局

[简体中文](spatial.md) · [English](../../../en/reference/language/spatial.md) · [文档首页](../../README.md)

区分程序计算的空间、逻辑时间、像素源码结构和编辑器布局，是使用空间语义的前提。

## 规则与接口

PixelLang 区分以下四种坐标与关系，不在它们之间隐式转换：

| 模型 | 是否影响程序行为 | 含义 |
| --- | --- | --- |
| 原生空间 XYZ | 是 | `frame`、`Point3`、`Vec3`、实体与类型化连接参与计算；见[原生空间模型](../../internals/spatial-model.md)。 |
| 逻辑时间 T | 是 | 事件与固定步长推进 flow 状态，独立于 XYZ；见[事件应用](../../guides/state-machines.md)。 |
| 语义像素源码布局 | 是 | PNG 的二维像素编码 token 顺序与语法结构，规则见下文。 |
| 编辑视图布局 | 否 | 组件图节点位置、相机、缩放与历史展示不改变程序。 |

下面的非负整数 `(X,Y)` 专指 PNG 源码像素坐标，不限制程序计算的三维空间。
时间轴可以作为历史视图的一个显示轴，但不能因此把原生空间的 Z 坐标解释为时间。

## 原生空间接口

接收者接口定义如下。
坐标系和负载类型来自接收者，调用时无需再次指定。

| 操作 | 行为 |
| --- | --- |
| `Space::[F, T, L](identity)` | 创建空快照；名称在一次运行中必须唯一 |
| `space.insert(position, payload)` | 保留负载值快照，分配递增身份，返回新快照 |
| `space.get(id)` | 返回该快照中的实体，不存在时产生执行故障 |
| `space.move(entity, position)` | 按身份移动实体，返回新快照 |
| `space.remove(entity)` | 删除实体，不复用其身份 |
| `space.neighbors(entity, radius)` | 含边界、排除自身的欧氏距离查询 |
| `space.ahead(origin, candidate, direction, tolerance)` | 判断候选实体是否在指定方向前方；容差使用空间长度单位 |
| `space.size()` | 返回实体数量 |
| `space.connect(source, target, payload)` | 创建有稳定身份的有向连接，保留负载值快照，返回新快照 |
| `space.getLink(id)` | 读取连接；已删除身份不能复用 |
| `space.disconnect(link)` | 删除指定连接，返回新快照 |
| `space.setLinkPayload(link, payload)` | 替换连接负载，保留连接身份与端点 |
| `space.outgoing(entity)` / `space.incoming(entity)` | 返回按身份排序的出边／入边 |
| `space.linkCount()` | 返回连接数量 |
| `space.source(link)` / `space.target(link)` | 在指定快照中按身份解析端点，取得该时刻的位置 |
| `link.id` / `link.payload` | 读取身份或负载；负载为声明类型的独立值 |
| `entity.id` / `entity.position` / `entity.payload` | 读取实体值；负载为声明类型的独立值 |
| `point.distance(other)` / `vector.dot(other)` | 同坐标系的位置距离与位移点积 |
| `transform.point(point)` / `transform.vector(vector)` | 显式转换位置或方向 |
| `first.then(after)` | 先应用 first，再应用 after |

`ahead` 将方向归一化，再比较位移的方向投影是否严格大于 `tolerance`；该参数不是查询半径。
删除实体前须断开所有关联连接。空间方法返回值，不原地修改接收者；例如
`let disconnected = scene.disconnect(link)` 不会改变 `scene`。

属性内部也不可通过赋值、容器修改或 `inout` 写入。`var data = link.payload` 创建可独立修改的本地值；
修改后调用 `scene.setLinkPayload(link, data)` 得到新快照。实体属性读取该实体值保存的位置与负载；
空间查询和移动则在接收者快照中按身份解析实体。

## 显式紧凑区域

region 自环标记区域头。每个语义 token 必须属于一个显式标记的区域；没有标记的非空源码被拒绝：

1. 标记唯一，区域按头的 `(Y,X)` 排序，同一物理行可有多个语句。
2. 没有 next 的行按 X 递增且相邻距离最多三；新标记开启新区。
3. 某行存在 next 时，该行每个区域都使用显式链。头不能有入边，链不能分叉、合并、成环或跨行；
   每个语义 token 恰好属于一个区域。
4. 默认模块级，只有 child 指定包含。父区域在区域序列中必须早于子区域，同 Y 也如此。
   每个子区域最多一个父，且只有语法块开启者能包含子语句；缩进不推导作用域。

发射器将完整区域装入横向分层布局，保留 token 顺序与父关系，相邻 token 不需要冗余 next 边。
标记和父边是结构源码，不在图片注释中保存 AST、完整文本或字节码。像素和尺寸限制仍有效。
物理缩进不决定作用域。移动区域时必须同步移动连接端点。

## 宿主调度

main 创建 flow 后，由宿主配置应用调度，不将 XYZ 解释为时间。见[原生事件应用](../../guides/state-machines.md)。

## 坐标与几何约束

坐标采用右手系，Y 向上，分量为有限 float64。支持 meters、centimeters、millimeters；同单位不同 frame 仍是不同类型。
Point3 表示位置，Vec3 表示位移：点减点得到位移，点加位移得到点，点加点不合法。
半径邻域包含边界、排除自身，按稳定实体身份排序；半径须非负有限。

`Transform3[From, To]` 的 `rotation` 是 3×3 正交旋转矩阵，`translation` 是 `Vec3[To]`。
变换不允许改变左右手性或隐式缩放，单位换算由 frame 单位确定；`first.then(after)` 先应用 first。
并行边和自环合法。身份删除后不复用，外来实体或不存在的身份导致执行故障。

## 继续阅读

- [完整文档目录](../../README.md)
- [语言总览](overview.md)
