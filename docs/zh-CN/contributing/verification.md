# 分层验证

[简体中文](verification.md) · [English](../../en/contributing/verification.md) · [文档首页](../README.md)

按变更范围选择 related、merge 或 release。产物验收还包括隔离安装和真实编辑器；任何一项都不能替代其他边界。


**本页内容**

- [测试组织](#测试组织)
- [日常相关修改](#日常相关修改)
- [合并前](#合并前)
- [发布前](#发布前)
- [性能预算](#性能预算)
- [恢复已完成阶段](#恢复已完成阶段)
- [并行调度](#并行调度)
- [GitHub CI 与安装包](#github-ci-与安装包)
- [原生空间安装验收](#原生空间安装验收)
- [原生编译器交付](#原生编译器交付)
- [继续阅读](#继续阅读)

## 测试组织

标准库效果、只读和值隔离契约集中在 `tests/test_stdlib_contracts.py`，共享一次服务初始化；
生成编译器门禁复用同一测试类并绑定候选产物。`tests/test_collection_callbacks.py` 保留
独立排序对照、PNG／源码恢复和回放边界，不另建重复的示例交付测试文件。

测试按当前语义或子系统命名，不按语言版本、迁移批次或开发阶段命名。
基础语法集中在 `tests/test_syntax.py`；闭包、泛型、错误、集合与编辑器分别归入对应主题。
相同主题且生命周期一致的用例共享类级编译服务，并在类清理阶段关闭。
不同执行器和交付边界仍需独立验证；主题归并不删除其断言。
移动用例时同步生成编译器验收入口和文档引用，核对测试方法清单与唯一测试 ID，
防止丢失用例或因导入 TestCase 而重复收集。不保留旧模块名的转发入口。

原生语言测试通过 `tests.native_compiler.native_service` 配对字节码与可执行文件。
源码变化时，使用内容寻址的开发缓存构建匹配的原生编译器；缓存同时绑定代码生成器、
运行时头文件、平台和 C 编译器身份。并行测试通过进程锁共享构建，损坏的产物重新构建。
这些测试产物不替换安装包，也不能作为发布验收收据。服务所属测试类必须注册清理。

像素 AST 往返测试按类复用配对的原生驱动；普通回归与生成编译器门禁共用请求和断言，
门禁只替换执行入口以绑定指定候选。小规模成功、拒绝与像素解码案例保留参考 VM 差分；
真实项目继续验证独立预期输出与文件效果，不通过减少案例来缩短执行时间。

解析器语法与拒绝用例集中于 `test_selfhost_parser.py`，并参与生成编译器验收。
`test_parser_backends.py` 只负责独立组件与完整编译器的 AST 一致性、编辑恢复及原生／VM 差分；
按类复用原生驱动，不把旧编译器作为迁移正确性的依据。

## 日常相关修改

```sh
.venv/bin/python scripts/verify_preview.py related --output /tmp/pixel-related-NEW --test tests.test_compiler_service
```

至少显式指定一个模块/类/方法，仅验证选择范围，不隐式执行完整自举或宣称全项目验收。

## 合并前

```sh
.venv/bin/python scripts/verify_preview.py merge --workers 2 --output /tmp/pixel-merge-NEW
```

运行宿主语言/项目测试（非 test_selfhost* 模块）、生成编译器的检查/IR/恢复/服务/缓存测试、
真实 Node Bridge、无界面 DAP 和安装服务性能预算。安装产物与清单必须匹配当前编译器源码。
完整编译器源码检查/降低留给自举；发布另需真实 VS Code 扩展宿主测试。

## 发布前

```sh
.venv/bin/python scripts/verify_preview.py release --workers 2 --output /tmp/pixel-release-NEW
```

包含合并门禁，再执行三代完整产物比较、完整无缓存编译器 PNG 导出/恢复、生成编译器读图、完整源码恢复后自编译，以及恢复编译器回归。源码恢复门禁必须重新生成编译器、由该编译器再次编译恢复源码并得到相同字节码，再执行冒烟程序；仅从 PNG 取出可执行字节码不能代替这一步。
安装产物必须与接受的 stage3 一致。此命令成本高，日常用 related。
编译器发布门禁不替代 wheel/VSIX 安装、真实编辑器和对应同一快照的发布检查。

CI 为每次 C 编译设置 `PIXELLANG_NATIVE_BUILD_TIMEOUT=600`，为每组回归设置 `--regression-timeout 3600`；本地默认值分别为 120 和 1200 秒。外层调度额外保留 30 秒用于清理工作进程和写入验收结果。这些基础设施期限不改变性能阈值或测试覆盖范围。

## 性能预算

```sh
.venv/bin/python scripts/benchmark_service.py --output /tmp/pixel-performance-NEW.json --limits config/service-performance-limits.json
```

固定双文件负载测冷启动/检查、热检查、修改、编译、PNG 和子进程峰值内存，恢复前后都验证执行。
阈值是回归上限，留出机器噪声空间，不承诺 UI 延迟，也不代表完整编译器负载。
同时限制时间与 VM 指令；内存为已回收子进程高水位，按 macOS 字节/Linux KiB 归一化。
记录电源与低电量设置，正式对照须同条件、同负载。
门禁测试包含故意失败的指令预算、真实测试失败传播、输出目录保护、非有限/不完整阈值拒绝。

## 恢复已完成阶段

```sh
.venv/bin/python scripts/verify_preview.py merge --workers 2 \
  --resume-from /tmp/pixel-merge-PREVIOUS --output /tmp/pixel-merge-RESUMED
```

必须使用新目录。源码/工具/测试哈希、门禁选择、并行度、Python/平台/依赖及规范命令必须相同。
只复制完成且成功、全部输出哈希完整的阶段，损坏/缺失或变化输入不复用，失败阶段重跑。
摘要记录 reusedFrom、originalSeconds；源输出不改写。
性能总是重跑，fresh: false 表示使用过恢复结果。完整新鲜发布自举/PNG 不传 --resume-from。
没有阶段收据不能恢复，任何运行时/编译器/测试输入变化会使旧门禁整体失效。
这些输出是用户指定的运行数据，项目文档不保留历史副本。

## 并行调度

related 和 merge 均支持 --workers。同一测试类保持在同一进程连续执行，避免重复运行昂贵的类初始化；
进程数是上限，只有一个测试类时只启动一个进程。独立测试类按历史耗时或用例数量分配，
编译器代际按依赖顺序，性能阶段等回归进程退出后运行。

```sh
.venv/bin/python scripts/verify_preview.py merge --workers 2 \
  --timings-from /tmp/pixel-merge-PREVIOUS --output /tmp/pixel-merge-NEW
```

旧测试耗时只平衡分片，不跳过测试、不复用通过状态。比较进程数时保持测试集和调度耗时来源一致，
同时记录时间和内存。各子进程峰值之和不代表同时总内存。--resume-from 是独立显式机制。

完整自举语料的词法、解析和规划测试使用与字节码绑定的原生驱动；同一批输入只准备一次驱动，逐次执行仍有独立的资源限制。小规模正常与错误输入保留原生／VM 差异测试，完整语料继续核对词法参考结果或 AST／规划约束。驱动复用不复用测试结果，不能将测试加速当作 Studio 编译延迟的测量。

## GitHub CI 与安装包

PR 和 main 推送运行 `.github/workflows/ci.yml`：Linux/Python 3.11 与 macOS/Python 3.13 回归、双语文档检查、扩展检查、打包、干净安装与真实 VS Code 验收。完整自举与 PNG 往返由 `full-acceptance.yml` 手动或每周单独执行。固定哈希引用 GitHub 官方 Actions；失败日志与验证结果保存在 workflow artifacts 中。

`preview-release.yml` 手动运行全部发布检查，成功后提供可下载的安装包。推送 `config/release.json` 声明的 alpha 标签时，运行相同检查，全部成功后才创建 GitHub 预览版。普通推送和手动验证不会发布版本；Marketplace 发布另行处理。

```sh
.venv/bin/python scripts/build_distribution.py --output /tmp/pixel-distribution-NEW
.venv/bin/python tests/clean_install.py /tmp/pixel-distribution-NEW/*.whl --report /tmp/pixel-clean-NEW.json
```

`config/release.json` 明确映射 Python 的 `0.9.0a1`、扩展的 `0.9.0` 和预览标签 `v0.9.0-alpha.1`。产物包含 wheel、VSIX、安装包/编译器身份清单 `release.json` 与 `SHA256SUMS`。打包验证两种安装包中的许可证、编译器和版本，源码变化时失败。打包成功本身不代表完整发布验收通过。

## 原生空间安装验收

原生空间验收另外检查安装后的运行时，不能用仓库源码导入代替：

```sh
.venv/bin/python scripts/verify_native_install.py \
  --wheel /tmp/pixel-distribution-NEW/*.whl \
  --compiler pixellang/artifacts/compiler.json --report /tmp/pixel-native-install-NEW.json
```

脚本创建隔离环境，校验安装路径和编译器哈希，运行三维邻域、带权路径和多文件原生电梯程序。
它生成 PNG 后删除原源码，再运行图片、恢复源码，并修改恢复的连接权重验证新输出。
这项检查不替代完整编译器自身的 PNG 交付验证。

VSIX 使用另一个隔离 VS Code 配置安装；`python` 参数指向上一步报告中环境的
`env/bin/python`：

```sh
node vscode/scripts/test-installed.js \
  /tmp/pixel-distribution-NEW/pixellang-studio-0.9.0.vsix \
  /path/to/isolated/env/bin/python pixellang/artifacts/compiler.json \
  /tmp/pixel-native-vsix-NEW.json
```

脚本核对扩展位于隔离安装目录、附带编译器与指定产物一致，再运行原生空间
扩展宿主验收。覆盖连接负载分页、历史回退、未保存坐标修改、过期调试视图拒绝
及重新运行后的邻域结果；桌面鼠标拖动、键盘撤销仍需单独的真实交互验收。

通过 `PIXELLANG_TEST_SUITE` 选择安装后的专项验收。每次使用新的报告路径；
编辑器测试共享桌面，应顺序运行。VSIX 和隔离 Python 环境沿用上述输入。

| 测试集 | 覆盖范围 |
| --- | --- |
| `native-spatial-debug`（默认） | 空间调试、坐标编辑及重新执行 |
| `native-graphs` | 未保存的图编辑、过期操作、撤销与投影刷新 |
| `native-computed` | 计算坐标、源码定位及防止字面量覆盖表达式 |
| `native-format` | 未保存文本格式化、缺失导入、幂等性与过期响应 |
| `application` | 类型化事件、录制回放、事件调试、持久化日志 |

例如在安装测试命令前添加 `PIXELLANG_TEST_SUITE=native-format`，并指定独立的
格式化报告路径。`native-spatial-pointer` 等待真实桌面拖动与键盘撤销；
启动该测试不代表交互验证成功，必须检查最终通过结果。

## 原生编译器交付

默认安装路径要求原生可执行文件、源码映射、编译器字节码和匹配的清单。
产物缺失、被修改或平台不匹配时明确失败。每个目标平台分别构建，
需要 Python、C11 编译器和 zlib 开发头文件。使用仓库中的字节码种子
生成原生种子时，不需要启动已安装的编译服务：

```sh
.venv/bin/python - <<'PYTHON'
import json
from pathlib import Path
from pixellang.native_codegen import build_native
code = json.loads(Path("pixellang/artifacts/compiler.json").read_text())
build_native(code, "build/native-seed/compiler", optimization=1, max_depth=1024)
PYTHON
.venv/bin/python scripts/verify_native_selfhost.py \
  --seed pixellang/artifacts/compiler.json \
  --native-compiler build/native-seed/compiler \
  --output build/native-acceptance
.venv/bin/python scripts/stage_native_compiler.py \
  --acceptance build/native-acceptance --output build/native-bundle
```

每次验收使用新的输出目录。编译器使用 `optimization=1` 并关闭函数内联，
压缩临时值的栈空间，同时控制大型 C 产物的优化成本。未优化的编译器可能在返回
深层语法诊断之前耗尽原生栈，因此 `optimization=0` 不用于编译器验收。
验收检查连续编译器代际、完整 PNG 导出与恢复、恢复编译器的自编译及冒烟程序。

暂存操作不会激活或发布产物。先备份 `pixellang/artifacts`，再将已验证目录中的
四个文件复制到其中，最后更新清单。运行
`scripts/build_distribution.py --output <新目录>` 构建并检查平台 wheel 与 VSIX。
发布前仍须完成隔离安装及 Studio 验收。macOS 最低系统版本来自实际二进制；
本地 Linux wheel 不代表具备 manylinux 可移植性。

独立的原生文本恢复门禁从 PNG 恢复编译器入口依赖的全部模块，再编译恢复文本、
构建机器码编译器，并要求它再次编译恢复源码得到完全相同的产物。还要求它编译原始源码快照后得到原始候选字节码（`canonicalCompilerEqual: true`），仅能自洽地复现恢复源码不足以通过。源码快照还可
包含其他独立入口，它们不属于该 PNG。门禁使用原生解析器确定预期模块范围，
检查所有输入指纹，并分别在参考 VM 和机器码后端运行恢复编译器生成的冒烟程序，要求两者均输出预期结果。
完整编译器源码恢复统一使用这一门禁，不再通过参考 VM 重复执行整编译器自编译。使用新的输出目录，
仅当 `summary.json` 中 `verified: true` 时才算验收通过。

```sh
.venv/bin/python scripts/verify_native_source_recovery.py \
  --compiler build/native-acceptance/compiler-g3.json \
  --native-compiler build/native-acceptance/compiler-g3 \
  --image build/native-acceptance/compiler.png \
  --sources build/native-acceptance/sources.json \
  --output build/native-text-recovery
```

生成产物回归支持为编译服务测试指定匹配的原生候选编译器。发布验证流程会分别
传入已安装编译器和 PNG 恢复编译器对应的可执行文件。运行器校验字节码、可执行
文件和映射文件的绑定关系并记录哈希，不会把候选产物替换为已安装编译器。
检查 VM 状态或直接运行组件驱动的测试仍使用参考 VM，因此该选项不代表整个
测试集都已原生执行。

```sh
.venv/bin/python scripts/verify_generated_compiler.py \
  --compiler build/native-acceptance/recovered-compiler.json \
  --native-compiler build/native-acceptance/recovered-compiler \
  --sources build/native-acceptance/sources.json \
  --output build/recovered-regression --workers 4
```

## 继续阅读

- [完整文档目录](../README.md)
