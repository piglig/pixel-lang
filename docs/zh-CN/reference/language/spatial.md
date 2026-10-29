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

标识回答“是哪一个对象”，实体值与连接值保存“某个快照中的数据”。查询和更新使用 `EntityId[F, T]` 与 `LinkId[F, T, L]`；它们支持相等比较和映射键。通过所属空间取得标识，不能直接将字符串当作标识。

| 操作 | 行为 |
| --- | --- |
| `Space::[F, T, L](identity)` | 创建空空间；空间身份在一次运行中唯一 |
| `space.entityId(key) / space.linkId(key)` | 从业务键取得类型化标识，不插入对象 |
| `space.insert(key, position, payload)` | 插入实体；拒绝重复业务键 |
| `space.get(id) / space.getLink(id)` | 返回 Option[Entity]／Option[Link]；不存在时为 None |
| `space.require(id) / space.requireLink(id)` | 读取必须存在的对象；缺失为执行故障 |
| `space.move(id, position) / space.setPayload(id, payload)` | 返回新快照，保留实体身份 |
| `space.remove(id)` | 删除实体；仍有关联连接时拒绝 |
| `space.removeCascade(id)` | 同时删除实体及所有关联连接 |
| `space.neighbors(id, radius)` | 返回本快照的邻域实体，排除自身 |
| `space.ahead(originId, candidateId, direction, tolerance)` | 判断位移在指定方向上的投影是否超过容差 |
| `space.connect(key, sourceId, targetId, payload)` | 在已有实体之间插入有向连接 |
| `space.disconnect(id) / space.setLinkPayload(id, payload)` | 删除连接或更新连接负载 |
| `space.outgoing(id) / space.incoming(id)` | 按插入顺序返回出边／入边 |
| `space.size() / space.linkCount()` | 返回实体／连接数量 |
| `entity.id / entity.position / entity.payload` | 读取实体标识以及该实体值保存的位置和负载 |
| `link.id / link.source / link.target / link.payload` | 读取连接标识、端点标识和保存的负载 |
| `id.key` | 读取稳定业务键 |
| `point.distance(other) / vector.dot(other)` | 同坐标系距离／点积 |
| `transform.point(point) / transform.vector(vector)` | 显式转换位置／位移 |
| `first.then(after)` | 先应用 first，再应用 after |

业务键是 1–256 个字符的字符串，精确匹配且区分大小写。业务键以空间和标识类型为作用域。两个分支插入相同键表示同一逻辑对象；不同键即使坐标相同也不是同一对象。删除后再次插入相同键，明确表示重建同一逻辑身份；新对象应使用新键。

所有更新返回新快照。已取得的实体值和连接值保留原数据。读取特定快照中的端点时，使用 `space.get(link.target)` 或 `space.require(link.target)`。要求标识的参数不接受实体快照，应显式传入 `entity.id`。

外来空间标识、重复插入、更新不存在的对象会导致执行故障。`get` 和 `getLink` 对合法但在当前快照中缺失的标识返回 `None`。删除实体需先断开连接，或显式使用 `removeCascade`。

属性不可写，包括容器修改和 `inout`。可将负载复制到本地变量修改，再通过 `setPayload` 或 `setLinkPayload` 发布；旧快照不变。

`ahead` 将方向归一化，判断位移投影是否严格大于 `tolerance`；容差采用坐标系的长度单位。

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
半径邻域包含边界、排除自身，按插入顺序返回；半径须非负有限。

`Transform3[From, To]` 的 `rotation` 是 3×3 正交旋转矩阵，`translation` 是 `Vec3[To]`。
变换不允许改变左右手性或隐式缩放，单位换算由 frame 单位确定；`first.then(after)` 先应用 first。
并行边和自环合法。标识查找和缺失行为遵循上表的约定。

## 继续阅读

- [完整文档目录](../../README.md)
- [语言总览](overview.md)
