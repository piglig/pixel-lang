# 带权路径

[简体中文](README.md) · [English](README.en.md) · [文档](../../docs/zh-CN/README.md)

沿类型化连接逐步传播距离，比较修改连接前后的结果。

## 运行

先完成[安装](../../docs/zh-CN/guides/installation.md)，在仓库根执行：

```sh
pixelrun examples/weighted-routes/main.pxl
```

## 预期输出

```text
[0, 5, 20, -1]
[0, 5, 7, 21]
[0, 5, 7, 8]
[0, 5, 20, 1]
false
```

## 关键规则

Road{cost, enabled} 保存权重与启用状态。前三步完成 A → B → C → D 的距离传播；启用 A → D 后新建实例，距离变为 1。-1 表示尚不可达。示例固定四个连续身份、非负权重，不处理通用负环；连接变化后重新创建实例。路径代价来自权重，不由几何距离推断。

## 交付与恢复

```sh
pixelpack examples/weighted-routes/main.pxl -o weighted-routes.png
pixelrun weighted-routes.png
pixelunpack weighted-routes.png -o weighted-routes-restored
pixelrun weighted-routes-restored/main.pxl
```

恢复目录须为空，图片运行和恢复后的源码应与原程序一致。空间与状态规则见[空间参考](../../docs/zh-CN/reference/language/spatial.md) · [数据流](../../docs/zh-CN/internals/dataflow.md)。
