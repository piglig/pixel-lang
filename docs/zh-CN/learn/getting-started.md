# 从第一个程序开始

[中文](getting-started.md) · [English](../../en/learn/getting-started.md) · [文档首页](../README.md)

在仓库根运行 `uv sync --locked`，需要 Python 3.11+。保存以下内容为 hello.pxl：

```pixellang
fn main() {
    print("Hello, PixelLang!")
}
```

```sh
.venv/bin/pixelrun hello.pxl
```

## 数组与排序

```pixellang
import "std/sort.pxl" as sort

fn main() {
    let values = sort.Ints([9, 2, 7])
    for index, value in values {
        print(string(index) + ": " + string(value))
    }
}
```

let 不可重新绑定，容器内容仍可修改；重新赋值使用 var。函数与变量在编译时检查类型，
模块用相对路径导入，标准库以 std/ 开头。

## 多文件与交付

[数据报表示例](../../../examples/data-report/main.pxl)读取 CSV、排序和统计：

```sh
.venv/bin/pixel test examples/data-report
.venv/bin/pixel build examples/data-report
.venv/bin/pixel run examples/data-report/dist/program.png --input examples/data-report/measurements.csv
```

PNG 保存语义像素程序。`pixelunpack program.png -o empty-directory` 可恢复可编辑文件，不保留原注释和排版。
接着阅读[项目](../guides/projects.md)、[语言](../reference/language/overview.md)和[标准库](../reference/standard-library.md)。
