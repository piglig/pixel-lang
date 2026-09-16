# 运行时、诊断与回放

[中文](runtime.md) · [English](../../../en/reference/formats/runtime.md) · [文档首页](../../README.md)

`VM(bytecode,max_steps=100000,trace=False,max_depth=256)` 验证并复制字节码。
`step()` 执行一条指令并返回状态，`run()` 执行到结束或受限失败并返回输出副本。
停止后的 step 不再执行指令。
源码、可执行文件、可变状态与轨迹是不同对象，轨迹不改变源码像素。
调试快照暴露 PC、函数、操作数栈、内存、调用帧、堆、输出、指令数、停止标记和错误。

诊断基本字段为 `{phase,message,span}`，span 含源码、精确点及 min/max。
阶段包括 format、encoding、spatial、syntax、semantic、type、bytecode、runtime 和工具 I/O/请求。
语法错误指向 token/区域，语义和类型错误指向表达式/语句，运行错误使用当前指令来源。
文件/模式错误可无位置；依赖加载指向导入区域，循环依赖指向闭合环的导入边。

CLI 错误输出到 stderr，退出码 1；`--json` 提供机器可读诊断。
Studio 只高亮当前源码的点，外部模块位置以名称展示，预算错误保留部分轨迹和输出。
调试会话彼此独立，最多保留 16 个，最旧优先淘汰。

默认预算：100,000 指令、256 帧、每函数 65,536 槽、1,000,000 堆项和 100,000 堆对象。
输入、字符串和输出量约一百万字符/单位，精确计数见 `vm.py`。
宿主可配置正整数 max_heap_items、max_heap_objects；分配、数组/映射增长和 split 均遵守，程序不能自行调整。
Timeline 接受 1..1,000,000 步，默认每 256 次转移建检查点，检查点预算 16 MB，日志预算 32 MB。
检查点淘汰后可能从零回放，随机跳转没有普遍常数时间保证。

数据错误可捕获为 Error；断言、溢出、取消与资源限制不可捕获。
FileAccess 默认不授予读写权限，相对路径限制在授权目录内并拒绝符号链接穿越。
最多 8 MB 效果记录按顺序保存读取结果和写入结果，回放使用记录，不重复写入。
取消在指令之间和 Studio 服务批次之间检查，不能强制中断正在执行的宿主调用。

通用二进制转换：`utf8Encode(string) -> [int]`、`utf8Decode([int]) -> string`、
`float64Bytes(float64) -> [int]`、`float64FromBytes([int]) -> float64`。
字节是 0..255 的 int，浮点表示恰为八个大端 IEEE 754 字节，保留负零并拒绝非有限解码值。
UTF-8 严格拒绝过长编码和代理项。范围、长度、编码错误可捕获，代码包括 bytes.range、bytes.length、
text.invalid_utf8、numeric.range。堆与字符串限制仍有效；这些不是编译器或 PNG 专用捷径。
