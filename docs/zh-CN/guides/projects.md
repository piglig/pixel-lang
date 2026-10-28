# 项目、依赖与测试

[简体中文](projects.md) · [English](../../en/guides/projects.md) · [文档首页](../README.md)

用 pixel.toml 组织多文件项目，并显式声明本地依赖与测试。先完成安装，再在工具链环境中执行本页命令。


**本页内容**

- [创建项目](#创建项目)
- [命令](#命令)
- [清单格式 1](#清单格式-1)
- [本地依赖与完整性](#本地依赖与完整性)
- [验证](#验证)
- [文件效果夹具](#文件效果夹具)
- [VS Code 项目命令](#vs-code-项目命令)
- [项目编译缓存](#项目编译缓存)
- [继续阅读](#继续阅读)

## 创建项目

```sh
pixel doctor
pixel init hello-pixels
cd hello-pixels
pixel run .
pixel test .
pixel build .
```

使用 `--name` 指定项目名。创建操作拒绝覆盖已有文件；初始化不下载依赖。当前编译器的受控文件访问需要 Linux 或 macOS 等 POSIX 宿主。

## 命令

安装后使用 pixel（或 python -m pixellang）：

```sh
pixel lock examples/data-report
pixel test examples/data-report
pixel build examples/data-report
pixel run examples/data-report --input examples/data-report/measurements.csv
pixel run examples/data-report/dist/program.png --input examples/data-report/measurements.csv
```

默认项目为当前目录，build 写 dist/program.png（或 -o 路径），结果含 SHA-256。
run 也接受源码、图片和字节码。test 输出机器可读摘要，任一失败退出 1，空测试集报错；--max-steps 对各测试独立生效。

## 清单格式 1

```toml
format = 1

[project]
name = "report"
entry = "main.pxl"

[dependencies]
math = "../math-library"

[[test]]
name = "sample report"
entry = "main.pxl"
input = "fixtures/input.csv"
expected = "fixtures/output.json"
```

expected 是顺序打印值的 JSON 数组，整数和布尔严格区分。没有预期文件时程序可用 assert。
自动发现 tests/*_test.pxl（含子目录），每个测试使用新 VM；显式声明可为同入口配置多组输入。
源码、测试输入和预期路径限于项目内。发现排除 .git、.venv、node_modules、dist、__pycache__ 和隐藏目录。
项目与依赖合计最多 128 源文件，每文件最多 4 MB，仍受一百万字符解析限制；导入保持词法相对路径语义。

测试文件也可以声明多个 `fn test_name() { ... }` 函数；函数必须无参数、无泛型并返回 unit，该文件不声明 main。
每个函数作为独立测试执行，名称为 `tests/example_test.pxl::test_name`。没有 test_ 函数的文件仍以 main 运行。
原生编译器负责发现声明，并直接把所选测试作为程序入口，不插入生成的 `main`。
只有请求测试列表或执行测试时才进行发现；普通构建、运行和锁定项目不会为此解析测试文件。
使用 `pixel test . --list` 列出测试，`pixel test . --filter '*::test_name'` 筛选执行；筛选不到测试会报错。

## 本地依赖与完整性

依赖是具有 pixel.toml 的显式本地项目，允许相对路径指向项目源码根之外；没有网络注册表或隐式下载。
deps/ 是保留虚拟命名空间，不应手工创建内容。
根模块通过 `"deps/math/main.pxl"` 导入，src/main.pxl 则通过 `"../deps/math/main.pxl"`。
库可有嵌套依赖，挂载于其命名空间；循环及超过 16 层被拒绝。

pixel lock 为依赖清单和全部源码（含嵌套依赖）记录 SHA-256。构建、运行和测试拒绝缺失或过期锁；
无依赖项目可无锁，应用本地源码修改无需更新锁。依赖修改后显式更新并检查 pixel.lock。
锁路径与模块身份相对化，图片含可达模块，不嵌入绝对路径，执行不需依赖目录。
同源码、锁、编译器、scale 和编码环境产生相同图片字节，不承诺不同编码器版本字节一致。

## 验证

项目测试使用 `pixel test`。工具链打包与隔离安装属于贡献者流程，见[分层验证](../contributing/verification.md)。

## 文件效果夹具

`[[test]]` 可声明 `read_root = "fixtures"` 授予项目子目录读取。
`written = { "report.json" = "fixtures/report.json" }` 授予新临时目录写入，并逐字节核对输出。
每测试独立 VM 和目录。测试外运行仍需 CLI 显式目录授权，Ledger 展示这种用法。

## VS Code 项目命令

打开含 pixel.toml 的文件夹或其父目录，Studio 为当前源码寻找最近清单，即使编辑辅助模块也使用项目入口。
本地依赖身份/锁与 CLI 相同，分析用未保存缓冲覆盖磁盘源码。

- **PixelLang: Run a Test**：保存项目并选择一个测试运行，支持自动发现的 test_ 函数。
- **PixelLang: Save and Test Project**：保存项目文档，执行显式和自动发现测试，在输出面板列出结果。
- **PixelLang: Save and Build Project PNG**：保存、检查锁，生成 dist/program.png 并显示绝对路径与 SHA-256。
- **PixelLang: Save and Update Dependency Lock**：检查依赖变更后显式保存并更新锁。

保存范围包括项目、挂载源码和清单。普通 Studio 构建拒绝未保存的清单或依赖变化，不静默更新锁。
编辑器仍可分析未锁定依赖，配置解析使用磁盘清单。

## 项目编译缓存

`pixel run` 和 `pixel test` 在用户缓存目录复用项目字节码；每次运行仍创建新 VM，重新读取输入并执行授权文件操作。
缓存键覆盖完整源码、标准库、编译器、运行时代码与 Python 版本；依赖锁始终先验证。缓存损坏或不可写时重新编译。
最多保留 32 项、128 MB。`PIXELLANG_DISABLE_CACHE=1` 强制重新编译；`PIXELLANG_CACHE_DIR` 可指定隔离缓存目录。
缓存默认存于当前用户的缓存目录，不读取项目自带的缓存产物。

## 继续阅读

- [完整文档目录](../README.md)
- [安装工具链](installation.md)
