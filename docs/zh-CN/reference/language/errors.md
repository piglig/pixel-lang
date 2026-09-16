# 结构化可捕获错误

[中文](errors.md) · [English](../../../en/reference/language/errors.md) · [文档首页](../../README.md)

缺失值使用 [Option](enums.md)。catch 绑定不可重新赋值的 Error，字段均为只读字符串。

| 字段 | 含义 |
| --- | --- |
| kind | json、io、numeric、collection、text、value、user 等类别 |
| code | 可用于程序分支的稳定点分错误代码 |
| message | 人类可读消息，措辞不作为分支契约 |
| path | JSON 路径、请求的相对文件路径、集合键/索引，或空字符串 |
| operation | 失败操作，如 jsonDecode、read、write、parseInt |
| expected | 适用时给出预期类型/值，否则为空 |
| actual | 适用时给出实际类型/值，否则为空 |

JSON 代码包括 json.invalid_syntax、json.duplicate_key、json.type_mismatch、json.missing_field、
json.unknown_field、json.numeric_range、json.missing_key、json.index_bounds、json.structure_limit、json.cycle。
路径以 $ 开始，标识符字段用 .name，其他键用 ["quoted.key"]，数组用 [i]。
缺失字段路径指向该字段，jsonDecode 的解析失败使用 $；上下文在嵌套转换中保留且只附加一次。

文件代码包括 io.access_denied、io.not_found、io.invalid_path、io.invalid_file、io.too_large、
io.invalid_encoding、io.no_space、io.unsupported_host、io.failure。
分类基于显式能力/路径检查和操作系统错误码，不匹配消息文本。
文件观测记录结构化错误，回放时不再访问文件系统，即使外部状态已改变。

其他代码包括 numeric.invalid_text、numeric.range、numeric.division_by_zero、collection.missing_key、
collection.duplicate_key、collection.index_bounds、collection.empty、text.empty_separator、value.cycle、value.depth。
`fail(message)` 产生 user.failure，均使用相同 try/catch 控制流。

message、expected、actual 最多 256 字符，path 最多 1024，operation 最多 64。
文件记录预算计入结构化错误载荷。断言、算术溢出、VM 限制和取消不可捕获。
诊断 phase 与 Error.kind 是不同维度。
