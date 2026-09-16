# Python 的职责与实现边界

[中文](python.md) · [English](../../en/internals/python.md) · [文档首页](../README.md)

编译器自举与运行时脱离 Python 是不同目标。`selfhost/*.pxl` 实现词法、解析、绑定、类型、特化、IR、汇编及图片恢复。
固定编译器字节码由 Python PixelVM 执行，CLI/Studio 不在每次请求时重建编译器，也不隐式回退参考前端。
文中的“自举前端”不表示机器码执行。

| 层 | 主要位置 | 当前职责 |
| --- | --- | --- |
| 自举编译器 | `selfhost/` | 语言与图片编译规则 |
| 运行时 | `pixellang/vm.py`、`fileaccess.py`、`jsonvalue.py` | 指令、内存、通用原语和 I/O |
| 服务 | `compiler_service.py`、`compiler_worker.py`、`compiler_rpc.py`、`compiler_client.py` | 校验、缓存、进程、取消、预算、客户端 |
| 调试 | `recording.py`、`temporal.py`、`debug_artifact.py` | 状态、源码映射、回放、一致性 |
| 引导与参考 | `text.py`、`parser.py`、`semantics.py`、`backend.py`、`project.py` | 初始引导、差分验证和部分编辑工具 |
| 工程工具 | `scripts/`、`tests/`、`bootstrap.py`、`regression.py` | 构建、测试和测量 |

省略目录的模块均位于 pixellang。物理目录尚未按职责拆包；参考前端不能直接删除，
editor.py 仍处理不完整源码，空间编辑和格式化仍有宿主调用。
运行时和服务目前是必需依赖，工程脚本可以长期保留 Python。

原生 VM 迁移必须对同一字节码验证数值、字符串、别名、错误、预算、调试和回放。
替换执行循环不等于安装包脱离 Python，还需迁移服务与工具宿主。见[原生 VM 原型](native-vm.md)。
