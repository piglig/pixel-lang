# 多文件日志分析

[简体中文](README.md) · [English](README.en.md) · [文档](../../docs/zh-CN/README.md)

本示例在当前工具链中运行。先完成[安装](../../docs/zh-CN/guides/installation.md)；以下命令从仓库根执行。

六个 PixelLang 源文件负责配置解码、日历校验、逐行处理、时间筛选、分组和统计。输入文件采用 JSON Lines，每个非空物理行是一条记录。空白行忽略；数据错误记入报告并继续下一行，文件读取错误记为行号 0 并继续下一个文件。

## 运行

```sh
pixel test examples/log-analysis
pixel build examples/log-analysis
mkdir -p /tmp/pixel-log-output
pixel run examples/log-analysis/dist/program.png \
  --input examples/log-analysis/fixtures/config.json \
  --read-root examples/log-analysis/fixtures \
  --write-root /tmp/pixel-log-output
```

## 输入与校验

配置必须包含 `files`（相对路径数组）和 `output`（输出相对路径）。`since`、`until` 为可选字段，省略或 `null` 表示该侧无时间边界；提供时必须为有效时间戳。时间窗口包含 `since`、不包含 `until`。`minimum_level` 可省略或为 `null`；提供时只能为 `INFO`、`WARN`、`ERROR`，分别保留该级别及更高级别，非法配置在读取文件前失败。配置和事件均通过 `jsonDecode::[T]` 检查，不接受缺失必填字段或额外字段。

事件格式：

```json
{"timestamp":"2026-09-09T10:00:00Z","level":"INFO","service":"api","duration_ms":10,"message":"请求完成"}
```

时间戳只接受固定宽度的 UTC `YYYY-MM-DDTHH:MM:SSZ`，支持公历闰年校验，年份为 0001–9999，不接受小数秒、时区偏移或闰秒。级别为 `INFO`、`WARN`、`ERROR`；耗时为非负 int64，服务名不能为空。先校验、再按时间和最低级别筛选，所以不符合筛选条件的非法行仍列为问题。支持 LF、CRLF 和 CR 换行，保留物理行号。

报告统计事件数、ERROR 数量、排除数、耗时总和/float64 平均值/最大值，以及按服务、级别、分钟分组的数量。`problems` 保留文件名、从 1 开始的物理行号、错误消息、稳定错误代码 `code` 和字段路径 `path`。问题通过泛型稳定排序按文件名、行号排列；文件读取失败的行号为 0。总和溢出和运行资源限制会终止执行。文件按配置顺序处理，重复配置的文件会被重复计入。

`fixtures` 提供混合数据和空文件的独立预期结果。`tests/test_log_analysis.py` 使用 Python 的 datetime 和 Counter 独立核对生成数据，并检查世纪闰年和无重复写入的时间回放。

## 命令行与流式处理

```sh
pixel run examples/log-analysis --max-steps 10000000 \
  --read-root examples/log-analysis/fixtures --write-root /tmp/pixel-log-output \
  --arg=--directory --arg=. --arg=--level --arg=WARN
```

通过位置参数指定文件，或使用 `--directory` 递归发现 `.jsonl` 文件。`--output` 默认为 `report.json`，
`--since`/`--until` 设置 UTC 时间范围，`--help` 显示用法。这些程序选项通过宿主的重复 `--arg` 传入。
普通执行以 64 KiB 分块读取，不保留输入历史；每个物理行最多 262144 字符。聚合表和问题列表仍受 VM 堆预算约束。
实现使用 `io.Lines` 与 `io.NextLine(&reader)` 显式推进读取位置，通过 `inout` 更新普通值报告与问题列表，不依赖可写闭包捕获。
大输入需显式增加 `--max-steps`。导出的 PNG 接受相同参数；没有程序参数时仍使用 JSON 输入配置。
