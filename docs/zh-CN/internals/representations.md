# 参考 AST 与规范表示

[中文](representations.md) · [English](../../en/internals/representations.md) · [文档首页](../README.md)

本页适用于 Python 引导/参考编译器及空间检查工具。自举编译器采用模块局部类型化节点 arena，
见[编译器接口](compiler-interfaces.md)，不要把两种内存模型混为一谈。

空间 AST 包含 dimensions、regions、connections 和 semantic_structure 树。
区域保留 ID、精确点、包围盒、父 ID、显式父标记和有序 token，保留位置、连接与语义分组。
规范 AST 使用 `Node(kind,data,span)`，覆盖声明、控制流、表达式、容器构造/访问/修改、内建调用和类型化 JSON。
let 带 mutable，fn 带 entry，for 可有第二绑定；else 成为 if 的 otherwise 列表。
语义分析记录 _type、_slot、_function、_locals、_region，_else_span 保留另一分支来源；
参数保留名称与类型，来源贯穿检查、降低、汇编。

Span 含源码身份和精确坐标，序列化另含分量 min/max。复合表达式合并操作符与操作数位置，语句涵盖整行。
最小运行时表达式范围可不含括号/调用标点，但区域中仍保留它们。

## 规范化

normalize(source) 解码解析后，把 AST 编码为规则网格：

- 区域按语义顺序位于 Y=0,2,4,…。
- 首 token 的 X 为嵌套深度两倍，后续间隔二。
- 一元/二元表达式完整加括号，独立表达式写为丢弃结果的 do。
- 关系图转为默认布局；移除非语义布局注解和多余透明单元，可保留认可的名称/来源注解。
- 原生 JSON 按键排序以获得稳定字节。

canonical.fingerprint(tree) 输出确定性语义 JSON，忽略位置和内部分析注解。
它规范布局，不证明常量折叠、重命名或交换律等任意语义等价。
规范化须保持执行与模块引用且幂等；导入格式化无需解析依赖，但语义编译仍验证依赖。
原文件与规范文件字节无需相同。

AST 还包含枚举、变体、match、模式绑定、函数值、调用和 lambda；类型树组合表达泛型和函数签名。
捕获从词法声明重算，内部注解不能替代像素。
文本 AST 在收集上下文时可暂缺匿名参数/结果类型，表达式匿名体转 return 并保留范围。
发射像素前所有执行签名必须解析，像素解码不依赖缺失文本注解。
