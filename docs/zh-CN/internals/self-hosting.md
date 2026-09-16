# 自举与自托管验证

[中文](self-hosting.md) · [English](../../en/internals/self-hosting.md) · [文档首页](../README.md)

Python 参考编译器把 selfhost/*.pxl 编译为 stage0 种子。PixelLang 编译器再使用同一冻结源码产生 stage1、stage2、stage3。
固定点比较完整规范字节码，而非只比较示例输出。安装 CLI/Studio 使用固定产物，不逐请求重复自举。
Python 提供 VM、传输和通用有界文件/字节/压缩操作，不在种子之后暗中执行编译前端。

## 验证要求

1. 明确源码、模块、token、AST、绑定、类型、IR 的[接口](compiler-interfaces.md)。
2. 词法覆盖完整语言、注释和 Unicode，并精确定位非法输入。
3. 解析覆盖声明、优先级、控制流、泛型、闭包和导入，能解析全部编译器源码。
4. 语义检查覆盖全部源码及作用域、绑定、类型推导、捕获、控制流的正反例。
5. 完整编译器经语义像素 PNG 导出、读取、解析、检查、降低和汇编后，能编译运行新程序；图片不藏可执行源码、AST 或字节码。
6. 同一源码生成三代产物并比较完整内容；生成编译器执行语言和多文件算法、字符串、排序、统计项目，独立预期包含输出和文件效果。

单个输出 42 的 smoke 不能代替真实项目验证，原生机器码/WASM 后端不是自举的必要条件。

## 命令与产物身份

在仓库根目录使用 .venv，每次写入新的外部目录：

```sh
.venv/bin/python -m pixellang.bootstrap --output /tmp/pixel-bootstrap-NEW
.venv/bin/python -m pixellang.bootstrap_image --output /tmp/pixel-image-NEW
.venv/bin/python scripts/verify_generated_compiler.py --compiler /tmp/pixel-bootstrap-NEW/stage3.json --sources /tmp/pixel-bootstrap-NEW/sources.json --output /tmp/pixel-generated-NEW
```

生成编译器回归不构建种子，禁止调用参考前端；检查源码是否与当前树匹配，记录测试选择和省略项。
两个完整源码检查由自举覆盖，其余检查/IR/恢复/服务/缓存测试按运行器选择执行。
已有完整图片可使用 verify_compiler_image.py 的 --compiler、--sources、--image、--output 重验，
然后对恢复的 compiler.json 运行生成编译器回归。完整发布流程见[验证指南](../contributing/verification.md)。

记录源码/运行时哈希、资源、电源、命令、错误和结果。规范化只允许改变 JSON 键顺序/空白，
不得省略指令、常量、类型、来源或数组顺序。改变输入需要重新验证；中断或未完成不算通过。
验证输出生成到仓库外，不随文档保留历史运行记录。
