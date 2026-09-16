# PixelVM 字节码（0.8）

[中文](bytecode.md) · [English](../../../en/reference/formats/bytecode.md) · [文档首页](../../README.md)

VM 验证并复制 PIXELVM 可执行文件，language 和 VM 版本轴都须为 0.8；compiler 版本仅为信息。
文件包含常量、名义记录模式（含受验证的内建 Error）、具体枚举模式、入口，以及带参数与捕获单元签名的函数体。

机器状态包含调用帧（PC、内存、操作数基址、处理器）、共享操作数栈、类型化堆引用、常量、输出、指令数及停止/错误状态。
入口为唯一无参数 unit 函数。返回检查帧平衡，unit 使用内部哨兵值；嵌套调用不能弹出调用者的操作数。
堆包含数组、映射、记录、枚举载荷、函数环境与共享绑定单元；json 不可变并带标签。

指令包括 PUSH、LOAD、STORE、BIND、CAPTURE；受检查算术/比较/逻辑；DUP/DUP2/POP/PRINT；
CALL/FUNCTION/CLOSURE/INVOKE/RETURN；JUMP/JUMP_IF_FALSE/JUMP_IF_TRUE/TRAP；
ARRAY/MAP/RECORD、ENUM/ENUM_IS/ENUM_GET、INDEX/INDEX_SET、FIELD/FIELD_SET；BUILTIN；
TRY/END_TRY；显式单/双模式 ITER_SNAPSHOT；带目标类型的 JSON_DECODE。
精确参数与动态行为见 `pixellang/vm.py` 和 `pixellang/backend.py`。

验证拒绝未知版本/操作码、畸形模式/常量、无效槽/常量/跳转、未知调用与非法入口签名。
运行时检查类型、未初始化内存、帧边界、整数溢出与预算。
外部字节码并不因此获得完整静态安全证明；非法执行产生结构化诊断，不作为宿主代码执行。
GC 从帧、操作数栈和临时分配根标记后清扫，确定性身份与完整检查点支持跨 GC 回放。
预算与错误见[运行时](runtime.md)。
