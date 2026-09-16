# RGBA 语义编码（0.8）

[中文](encoding.md) · [English](../../../en/reference/formats/encoding.md) · [文档首页](../../README.md)

A=0 表示空单元，A=255 表示语义单元；部分透明和未知颜色报错。
载荷 N=256*G+B 根据类别解释，不直接作为 VM 操作码。

| R | 含义 |
| --- | --- |
| 16 | 有符号 16 位字面量 |
| 17 | 布尔载荷 0/1 |
| 18 | int64 头，后接四个大端数据字 |
| 19 | UTF-8 字节长度，后接大端字节对 |
| 20 | 有限 binary64 头，后接四个大端数据字 |
| 32 | 无符号 16 位标识符 |
| 48 | 操作符表索引 |
| 64 | 关键字表索引 |
| 80 | 标点表索引 |
| 96 | 类型表索引 |
| 112 | 16 位字面量延续字 |
| 128 | 内建操作表索引 |

当前表为 `pixellang/codec.py` 的 OPS/KEYWORDS/PUNCT/TYPES 和 `pixellang/typesys.py` 的 BUILTINS。
解析器重建字面量并验证 UTF-8、长度和整数头。var 与 entry 是可执行关键字。
双绑定 for 用逗号，jsonDecode 携带目标类型。

language/encoding/file 版本轴均为 0.8，其他版本拒绝。
`picture.py` 的显示调色变换无损可逆，与语义 RGBA 分离；精确坐标和关系边仍参与语义。
泛型参数与实参、枚举与构造器、穷尽 match、匿名函数体引用均为可执行 token。
匿名函数体存于独立空间区域并重建词法 AST，从体内推导捕获。见[函数](../language/functions.md)、[枚举](../language/enums.md)。
