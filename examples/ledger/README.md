# Ledger — PixelLang 数据处理项目

[中文](README.md) · [English](README.en.md)

Ledger 的配置解析、CSV 解析、逐行校验、筛选、分组、统计、排序和报告生成均由 PixelLang 编写。宿主仅提供通用 JSON 值操作、受控文件读写和语言运行时。

在仓库根目录执行：

```sh
uv sync --locked
.venv/bin/pixel test examples/ledger
.venv/bin/pixel build examples/ledger
mkdir -p /tmp/ledger-output
.venv/bin/pixel run examples/ledger/dist/program.png \
  --input examples/ledger/fixtures/config.json \
  --read-root examples/ledger/fixtures \
  --write-root /tmp/ledger-output
```

报告写入 `/tmp/ledger-output/report.json`，同时打印到标准输出。PNG 包含应用和标准库，不需要原来的 `.pxl` 文件。运行时和显式授予的数据目录仍然需要存在。

## 输入与规则

CSV 表头为 `category,amount_minor`。金额使用整数最小货币单位，范围为 ±1,000,000,000,000。类别首尾空白会清理，空类别和非法整数会记为错误行。错误行号按 CSV 逻辑记录计数，表头为第 1 条；引号中的换行不会额外增加记录号。

配置必须包含 `source` 和 `output`；`categories`、`minimum_minor` 可省略或设为 `null`，默认分别为全部类别和 0：

```json
{"source":"sales.csv","output":"report.json","categories":[],"minimum_minor":0}
```

`source`、`output` 分别相对于读取和写入权限目录。空的 `categories` 表示全部类别；`minimum_minor` 是包含端点的金额下限。先校验数据，再筛选，因此被排除类别中的非法行仍计入错误。

可选的 `maximum_minor` 金额上限，包含端点；省略或 `null` 表示无上限。上限小于下限时，在读取 CSV 前报错。例如 `"minimum_minor": 100, "maximum_minor": 500` 接受 100 至 500 的金额。

报告包含 `accepted`、`rejected`、`filtered`、`groups` 和 `errors`。组按总额降序排列，总额相同按类别的 Unicode 字符顺序升序排列。每组包含数量、总额、最小值、最大值和 float64 平均值（仍以最小货币单位计量，可包含小数；金额、总额及阈值保持整数）。空文件和仅表头文件产生空报告。CSV 语法错误、错误表头、配置错误和文件访问失败终止本次任务；单行数据错误不会终止后续行处理。

## 验收数据

- `fixtures/bounded-config.json`：金额范围为 2 至 3，接受两个端点各一行，筛除其余三行；使用独立报告预期验证。

- `fixtures/sales.csv`：合法行、负数、空字段、非法数字、金额超限、列数错误、带逗号的类别和多行类别。预期接受 6 行、拒绝 5 行、筛除 2 行。
- `fixtures/changed.csv`：不同输入及总额并列，预期接受 5 行、无错误。
- `fixtures/empty.csv`：空输入，预期空报告。

各场景的打印结果与磁盘报告都有独立的预期文件。`pixel test` 为每个场景创建临时输出目录，校验写出的文件并清理。`tests/test_ledger.py` 另测类别筛选和真实应用的时间回放；`tests/clean_install.py` 安装 wheel 后测试、构建，再删除应用与已安装标准库源码，用交付图片处理新输入。

VS Code 中打开本目录，设置 `pixellang.readRoot` 为 `fixtures`，将 `pixellang.writeRoot` 设为一个已存在的报告目录。Studio 的 Runtime input 填入上面的 JSON 配置即可运行。也可在 F5 的 launch 配置中用 `input` 提供配置文本。Python 解释器需安装 PixelLang 的运行依赖。

项目使用 `Option[T]` 解码可选配置，并以 `collections.Sort` 和匿名比较函数排序，由参数推导 Group 类型。平均值用于统计展示，不作为整数货币结算结果。
