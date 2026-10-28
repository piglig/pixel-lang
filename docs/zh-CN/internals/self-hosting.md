# 自举与源码恢复

[简体中文](self-hosting.md) · [English](../../en/internals/self-hosting.md) · [文档首页](../README.md)

自举验证要求编译器编译自身，并在固定源码上达到完整产物固定点。PNG 和文本恢复分别验证交付链路。

## 固定点流程

先用种子编译当前源码，准备第一代候选字节码与匹配的原生可执行文件。原生自举门禁
以该候选为 `--seed`，使用同一冻结的 `selfhost/*.pxl` 源码生成第二、第三代，
每代都必须与上一代完整相等。验证记录种子路径和 SHA-256，并检查输入未改变。
固定点比较完整规范字节码，而非只比较示例输出。安装 CLI/Studio 使用固定产物，不逐请求重复自举。
完整编译器验收由原生可执行文件执行；Python 负责调度、传输和产物校验，
参考 VM 保留小规模执行差分和调试验证。此路径不调用 Python 编译前端。

## 验证要求

1. 明确源码、模块、token、AST、绑定、类型、IR 的[接口](compiler-interfaces.md)。
2. 词法覆盖完整语言、注释和 Unicode，并精确定位非法输入。
3. 解析覆盖声明、优先级、控制流、泛型、闭包和导入，能解析全部编译器源码。
4. 语义检查覆盖全部源码及作用域、绑定、类型推导、捕获、控制流的正反例。
5. 完整编译器经语义像素 PNG 导出、读取、解析、检查、降低和汇编后，能编译运行新程序；图片不藏可执行源码、AST 或字节码。
6. 同一源码生成三代产物并比较完整内容；生成编译器执行语言和多文件算法、字符串、排序、统计项目，独立预期包含输出和文件效果。

单个输出 42 的 smoke 不能代替真实项目验证，原生机器码/WASM 后端不是自举的必要条件。

## 命令与产物身份

在仓库根目录使用 .venv，每次写入新的外部目录。下面的 candidate 目录必须包含
当前源码的候选编译器、匹配的原生文件及映射；旧版本种子不能直接冒充第一代候选：

```sh
.venv/bin/python scripts/verify_native_selfhost.py \
  --seed /tmp/pixel-candidate/compiler.json \
  --native-compiler /tmp/pixel-candidate/compiler-native \
  --output /tmp/pixel-native-NEW
.venv/bin/python scripts/verify_native_source_recovery.py \
  --compiler /tmp/pixel-native-NEW/compiler-g3.json \
  --native-compiler /tmp/pixel-native-NEW/compiler-g3 \
  --sources /tmp/pixel-native-NEW/sources.json \
  --image /tmp/pixel-native-NEW/compiler.png \
  --output /tmp/pixel-recovery-NEW
.venv/bin/python scripts/verify_generated_compiler.py \
  --compiler /tmp/pixel-recovery-NEW/compiler.json \
  --native-compiler /tmp/pixel-recovery-NEW/compiler-native \
  --sources /tmp/pixel-native-NEW/sources.json \
  --output /tmp/pixel-generated-NEW
```

生成编译器回归不构建种子，禁止调用参考前端；检查源码是否与当前树匹配，记录测试选择和省略项。
两个完整源码检查由自举覆盖；生成编译器回归还运行词法与解析测试，包括全部编译器源码和示例，
以及检查/IR/恢复/服务/缓存、原生编辑与空间 PNG／执行记录测试。
编译器测试驱动通过 PixelLang 构建；源码哈希与随附产物一致时直接使用该产物，
其他驱动按源码、种子和运行时内容缓存，不依赖 Python 前端重建自举源码。
已有完整图片直接使用上面的原生源码恢复命令重验，无需重新导出 PNG；
输入图片、源码快照及配对编译器必须属于待验收版本。随后对恢复的编译器运行生成产物回归。
像素中间层的独立检查保留在 `test_selfhost_pixel_ast.py` 与 `test_selfhost_pixel_project.py`，
完整负载统一经过原生 PNG 门禁，不再另跑参考 VM 的完整像素编译器往返。完整发布流程见[验证指南](../contributing/verification.md)。

记录源码/运行时哈希、资源、电源、命令、错误和结果。规范化只允许改变 JSON 键顺序/空白，
不得省略指令、常量、类型、来源或数组顺序。改变输入需要重新验证；中断或未完成不算通过。
验证输出生成到仓库外，不随文档保留历史运行记录。

## 继续阅读

- [完整文档目录](../README.md)
- [编译器架构](architecture.md)
