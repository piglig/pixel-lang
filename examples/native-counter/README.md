# 独立计数器

[简体中文](README.md) · [English](README.en.md) · [文档](../../docs/zh-CN/README.md)

创建两个 flow 实例，观察状态如何独立累积。

## 运行

先完成[安装](../../docs/zh-CN/guides/installation.md)，在仓库根执行：

```sh
pixelrun examples/native-counter/main.pxl
```

## 预期输出

```text
1
3
10
6
```

## 关键规则

Counter() 创建实例，step(delta) 读取旧状态并原子提交。Flow[fn(int) -> int] 可作为普通参数传递；复制句柄不会创建独立计数器。

## 交付与恢复

```sh
pixelpack examples/native-counter/main.pxl -o native-counter.png
pixelrun native-counter.png
pixelunpack native-counter.png -o native-counter-restored
pixelrun native-counter-restored/main.pxl
```

恢复目录须为空，图片运行和恢复后的源码应与原程序一致。空间与状态规则见[空间参考](../../docs/zh-CN/reference/language/spatial.md) · [数据流](../../docs/zh-CN/internals/dataflow.md)。
