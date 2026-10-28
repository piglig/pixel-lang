# Python 的职责

[简体中文](python.md) · [English](../../en/internals/python.md) · [文档首页](../README.md)

自举编译器与宿主运行时分工明确。删除历史前端不意味着删除执行、调试和工程工具所需的 Python。

## 规则与接口

编译器自举与运行时脱离 Python 是不同目标。`selfhost/*.pxl` 实现词法、解析、绑定、类型、特化、IR、汇编及图片恢复。
CLI/Studio 默认通过随附的原生编译器执行编译，校验原生文件与编译器字节码的绑定。
它们不在每次请求时重建编译器，也不隐式回退参考前端。Python PixelVM 仍用于程序运行、调试和差异验证。

| 层 | 主要位置 | 当前职责 |
| --- | --- | --- |
| 自举编译器 | `selfhost/` | 语言与图片编译规则 |
| 运行时 | `pixellang/vm.py`、`fileaccess.py`、`jsonvalue.py` | 指令、内存、通用原语和 I/O |
| 服务 | `compiler_service.py`、`compiler_worker.py`、`compiler_rpc.py`、`compiler_client.py` | 校验、缓存、进程、取消、预算、客户端 |
| 调试 | `recording.py`、`temporal.py`、`debug_artifact.py` | 状态、源码映射、回放、一致性 |
| 参考解析 | `text.py`、`parser.py`、`spatial.py` | 独立解析与空间结构验证；不参与 CLI/Studio 编译 |
| 工程工具 | `scripts/`、`tests/`、`bootstrap.py`、`regression.py` | 构建、测试和测量 |

省略目录的模块均位于 pixellang。物理目录尚未按职责拆包。Studio 的不完整源码分析、
符号索引和格式化由原生编译器处理；`native_editor.py` 将索引提供给工作站，
`spatialedit.py` 使用原生 AST 生成源码编辑。旧 Python 编辑器实现已删除。
旧 Python 项目编译、语义检查、IR 降低、汇编和规范化前端已删除；自举使用随附的
编译器种子。保留的参考解析器承担独立组件验证，不能作为编译或编辑失败时的回退路径。
运行时和服务目前是必需依赖，工程脚本可以长期保留 Python。

原生编译器由 `native_codegen.py` 生成，值、访问、JSON 和文件操作由对应的 `native_*.h` 实现。
原生路径与 Python VM 必须对同一字节码验证数值、字符串、值隔离、错误和预算。
Python VM 的调试及回放职责仍然存在；移除旧的编译加速实验不等于移除这些运行时能力。

## 继续阅读

- [完整文档目录](../README.md)
- [编译器架构](architecture.md)
