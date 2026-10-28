# 标准库

[简体中文](standard-library.md) · [English](../../en/reference/standard-library.md) · [文档首页](../README.md)

标准库模块通过 std/ 路径导入。本页按任务索引接口；具体签名以模块中的 export 声明为准。

## 规则与接口

| 模块 | 用途 |
| --- | --- |
| [std/error.pxl](../../../pixellang/stdlib/error.pxl) | 构造带稳定代码和有界消息的标准错误 |
| [std/result.pxl](../../../pixellang/stdlib/result.pxl) | 内建 Result 的成功与错误映射、短路组合 |
| [std/collections.pxl](../../../pixellang/stdlib/collections.pxl) | 映射、筛选、归并、分组、稳定排序、按值查找与稳定去重 |
| [std/cli.pxl](../../../pixellang/stdlib/cli.pxl) | 程序参数与长选项解析 |
| [std/path.pxl](../../../pixellang/stdlib/path.pxl) | 相对路径清理、连接和文件名处理 |
| [std/csv.pxl](../../../pixellang/stdlib/csv.pxl) | CSV 数据处理 |
| [std/io.pxl](../../../pixellang/stdlib/io.pxl) | 授权文本文件操作 |
| [std/json.pxl](../../../pixellang/stdlib/json.pxl) | 动态 JSON 检查 |
| [std/stats.pxl](../../../pixellang/stdlib/stats.pxl) | 统计 |
| [std/text.pxl](../../../pixellang/stdlib/text.pxl) | 文本处理 |

## 流式文件与程序参数

`io.ReadChunk(path, offset, size)` 按字节偏移读取 UTF-8，返回 `Result[Chunk, Error]`，Chunk 包含 text、next、done；size 为 4–262144 字节，next 始终位于字符边界。
`io.ForEachLine(path, visit)` 以 64 KiB 分块读取，回调接收从 1 开始的行号与行内容；支持跨块 LF/CRLF/CR，保留末尾空行，单行最多 262144 字符。
需要累积状态时，使用 `var reader = io.Lines(path)`，在循环中调用 `io.NextLine(&reader)`。
它返回 `Result[Option[io.Line], Error]`；Line 包含 number 和 text，Ok(None) 表示结束，Err 表示读取失败。
只有成功读取行或到达结尾时才更新 reader；失败保留调用前的游标值。
创建游标不执行 I/O，`NextLine` 需要效果权限。游标是普通值，复制后分别推进各自的位置；
处理结果保存在显式局部变量中，不需要可写闭包捕获。`ForEachLine` 使用同一读取实现。
`ForEachLine` 与 `Walk` 返回 `Result[int, Error]`，成功值为已访问的行数或文件数。
`io.List(path)` 返回 `Result[[Entry], Error]`，成功时包含按名称排序的普通文件与目录；`io.Walk(path, visit)` 递归访问普通文件，跳过符号链接。空路径表示授权根目录。
目录枚举每次最多 10000 项且有名称预算；所有访问仍要求显式 read-root 授权。

`cli.Args()` 返回程序参数的独立数组。在宿主命令的 `--` 后传入程序参数，如 `pixel run . -- --level WARN`；也可使用重复的 `--arg`，如 `--arg=--level`。
`cli.Parse(values, flags, options)` 返回 `Result[Parsed, Error]`，成功值包含命名选项和位置参数，支持 `--name=value`、`--name value` 与结束标记 `--`；未知或重复选项返回带稳定 cli.* 代码的 Err。
`path.Clean/Join/Base/Dir/Extension` 返回 `Result[string, Error]`，操作相对 POSIX 路径，拒绝逃出根目录；清理路径不会授予文件权限。

普通 CLI 运行不保留历史读取内容。时间调试记录读取结果并受文件效果预算限制，恢复时不重新读取磁盘；大文件批处理应使用普通运行。

## 逻辑时间

[`std/time.pxl`](../../../pixellang/stdlib/time.pxl) 提供受检查的整数时长与固定步长时钟。契约和运行示例见[原生事件应用](../guides/state-machines.md)。

`Seconds`、`Milliseconds` 返回 `Result[Duration, Error]`，
`At` 返回 `Result[Step, Error]`。范围错误、无效间隔和累计时间溢出通过稳定的
`time.*` 错误码报告。`Nanoseconds`、`InSeconds` 返回普通值。

`collections.Repeat(value, count)` 返回 `Result[[T], Error]`。负数次数返回
`collection.negative_count`，零次返回空数组。重复元素遵循值语义，修改一个元素不会改变其他元素。

## 值语义与回调

集合算法使用普通泛型值和纯 `fn` 回调。`Filter`、`Unique`、`SortedBy` 返回 `[T]`，
`Map` 返回 `[U]`，`GroupBy` 返回 `map[K, [T]]`，其中 `K: Hashable`。`Fold` 使用 `fn(A, T) -> A`，
保留累加器类型 `A`；`Sum` 返回 `T`。排序、分组和重复值的局部修改不会改变输入。

可选查询使用 `table.get(key)`。普通赋值保证值隔离，无须显式复制集合。
`collections.Increment` 使用 `inout map[K, int]`，其中 `K: Hashable`；调用方式为 `collections.Increment(&counts, key)`。

`result.Map`、`MapError`、`AndThen` 直接保留或转换 `Result` 的类型参数。
外部效果通过 `effect fn` 声明；`io.ForEachLine` 与 `io.Walk` 接收 `effect fn` 回调。

发布须按[验证流程](../contributing/verification.md)绑定当前源码和产物；语言参考不保存单次验收状态。

## 数据处理结果

`csv.Parse` 返回 `Result[[[string]], Error]`，`csv.Encode` 返回 `Result[string, Error]`。
CSV 错误包含稳定 csv.* 代码和字符偏移（Encode 的空行错误使用行索引）。
JSON 解析、取字段、索引和形状检查返回 Result；确定性的构造、Kind 和 Stringify 返回普通值。
`stats.Summary` 返回 `Option[map[string, int]]`，空输入为 None。

## 继续阅读

- [完整文档目录](../README.md)
