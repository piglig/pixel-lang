# 第一个程序

[简体中文](getting-started.md) · [English](../../en/learn/getting-started.md) · [文档首页](../README.md)

我们先写一个小程序，运行它，再把它变成一张图片。最后，你会从图片恢复代码，并再次运行。
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

PixelLang 从 `main` 开始运行。这个函数要向终端打印文字，所以使用 `effect fn`：表示它可以与程序外部交互。

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

运行源码、PNG 和恢复后的代码，你应该看到相同的输出。请为 `recovered` 选择空文件夹。图片带上了程序需要的代码，接收者仍需要安装 PixelLang 才能运行。分享时保留原始 PNG，不要缩放或编辑它；恢复后的代码行为一致，但不会保留原来的注释和排版。

## 下一步

- [多文件项目](../guides/projects.md)：项目清单、依赖与测试。
- [语言总览](../reference/language/overview.md)：类型、声明与控制流。
- [三维空间](../guides/spatial-programming.md)：让实体位置和连接参与计算。
