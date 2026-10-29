# 三维邻域

[简体中文](README.md) · [English](README.en.md) · [文档](../../docs/zh-CN/README.md)

只修改实体的 Z 坐标，验证几何关系会改变程序输出。

## 运行

先完成[安装](../../docs/zh-CN/guides/installation.md)，在仓库根执行：

```sh
pixelrun examples/native-space/main.pxl
```

## 预期输出

```text
1
0
2.0
3.0
true
```

## 关键规则

Point3[World] 是位置，Vec3[World] 是位移。移动实体返回新快照，旧快照保持原位置。neighbors 包含半径边界、排除自身，并按插入顺序返回。实体值保存取得时的位置，空间查询接受类型化 EntityId 标识，读取指定快照。

## 交付与恢复

```sh
pixelpack examples/native-space/main.pxl -o native-space.png
pixelrun native-space.png
pixelunpack native-space.png -o native-space-restored
pixelrun native-space-restored/main.pxl
```

恢复目录须为空，图片运行和恢复后的源码应与原程序一致。空间与状态规则见[空间参考](../../docs/zh-CN/reference/language/spatial.md) · [数据流](../../docs/zh-CN/internals/dataflow.md)。
