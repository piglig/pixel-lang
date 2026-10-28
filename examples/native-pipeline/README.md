# 表达式数据流水线

[简体中文](README.md) · [English](README.en.md) · [文档](../../docs/zh-CN/README.md)

把数组翻倍、求和与组合计算组织为显式依赖。

## 运行

先完成[安装](../../docs/zh-CN/guides/installation.md)，在仓库根执行：

```sh
pixelrun examples/native-pipeline/main.pxl
```

## 预期输出

```text
24
16
```

## 关键规则

lib.pxl 定义 Pipeline；Combined 声明两个持久子实例，并合并它们的 step 输出。即使不含状态，flow 调用也创建实例。无状态普通计算使用 fn。依赖排序保证先求所需结果，不要求声明顺序等于执行顺序。

## 交付与恢复

```sh
pixelpack examples/native-pipeline/main.pxl -o native-pipeline.png
pixelrun native-pipeline.png
pixelunpack native-pipeline.png -o native-pipeline-restored
pixelrun native-pipeline-restored/main.pxl
```

恢复目录须为空，图片运行和恢复后的源码应与原程序一致。空间与状态规则见[空间参考](../../docs/zh-CN/reference/language/spatial.md) · [数据流](../../docs/zh-CN/internals/dataflow.md)。
