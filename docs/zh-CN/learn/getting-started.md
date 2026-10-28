# 第一个程序

[简体中文](getting-started.md) · [English](../../en/learn/getting-started.md) · [文档首页](../README.md)

本教程完成一次完整流程：编写文本程序、运行、导出 PNG，再从 PNG 恢复源码。
先完成[安装](../guides/installation.md)，并确认 `pixel doctor` 成功。以下命令在已激活的工具链环境中运行；源码安装也可使用 `uv run pixelrun` 等入口。

## 1. 编写与运行

新建 `hello.pxl`：

```pixellang
effect fn main() {
    print("Hello, PixelLang!")
}
```

```sh
pixelrun hello.pxl
```

```text
Hello, PixelLang!
```

`main` 是入口。`print` 产生外部效果，因此入口声明为 `effect fn`。

## 2. 使用集合算法

将文件内容替换为：

```pixellang
import "std/collections.pxl" as collections

effect fn main() {
    let values = collections.Sorted([9, 2, 7])
    for value in values {
        print(value)
    }
}
```

再次运行，依次输出 `2`、`7`、`9`。`Sorted` 返回排序后的新数组；`let` 绑定只读。
需要修改本地值时使用 `var`，其他副本保持不变。

## 3. 用图片交付

```sh
pixelpack hello.pxl -o program.png
pixelrun program.png
pixelunpack program.png -o recovered
pixelrun recovered/hello.pxl
```

三次执行应产生相同结果。恢复目录必须为空；PNG 包含入口和可达模块，执行仍需 PixelLang 运行时。
恢复生成规范源码，不保留原始注释和排版。传输图片时保持精确像素。

## 下一步

- [多文件项目](../guides/projects.md)：项目清单、依赖与测试。
- [语言总览](../reference/language/overview.md)：类型、声明与控制流。
- [三维空间](../guides/spatial-programming.md)：让实体位置和连接参与计算。
