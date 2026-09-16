# 编译服务协议（版本 1）

[中文](compiler-service.md) · [English](../../en/reference/compiler-service.md) · [文档首页](../README.md)

CLI 与 VS Code Studio 共用受监督的编译服务。

## 请求与结果

请求含 version: 1、id、revision、operation 及操作输入。id 关联请求，revision 标识源码快照；
响应回传两者及编译器 SHA-256。编辑器丢弃 revision 或源码与当前缓冲区不符的结果。

| 操作 | 输入 | 成功结果 |
| --- | --- | --- |
| check | files, entry | 结构化诊断，无产物 |
| compile | files, entry | bytecode |
| pixels | files, entry | 语义文档 |
| export-png | files, entry, scale | PNG 字节 |
| compile-png | PNG 字节 | PixelLang 恢复的字节码 |
| recover-png | PNG 字节 | 可编辑 files、entry |
| module-info | files, entry | 自举解析器的模块/导入信息 |
| debug-bundle | files, entry, includeIR=false, includePNG=false, scale | 字节码、调试元数据、映射文档，可选 IR/PNG |
| debug-compile | files, entry | 字节码和帧信息 |
| debug-pixels | files, entry | 带源码映射的语义文档 |
| export-debug-png | files, entry, scale | 带文件/符号名、不带源码映射的 PNG |
| inspect-ir | files, entry | 自举编译器 IR |

进程内 PNG 为 bytes，JSON-lines 传输为 `{"$bytes":"BASE64"}`；含保留传输键的字典会转义，避免与程序 JSON 混淆。
响应 status 为 ok、diagnostics、error、cancelled 或 timeout，含 diagnostics、可选 result/error 及 metrics。
诊断保留模块、阶段、Unicode 标量偏移和从一开始的行列；取消和超时代码为 service.cancelled、service.timeout。
指标记录耗时、指令数、解析模块数、命中和缓存字节。
编译请求不运行用户程序；编译输出位于隔离临时目录，仅成功后返回，执行和调试是独立操作。

## 监督、取消和限制

`python -m pixellang.compiler_rpc`（安装命令 pixelcompiler）维护持久子进程。
`--compiler PATH` 显式选择开发产物，否则必须使用安装编译器及清单，不隐式回退到 Python 前端。
一个子进程顺序处理请求，每次恢复可信空 VM 检查点，成功请求间保留解析、分析和 IR 缓存。
取消或超时终止子进程，下次重新启动；父进程监督补充协作检查，覆盖宿主原语。
管道仅传有界控制信息，大载荷走临时文件。

默认 120 秒、5 亿指令、6400 万堆项、200 万堆对象、6400 万输出/文件字节；可配置项均有有限上界。
VM 一百万字符输入和普通 JSON 预算不变，类型化解析数据通过有界分块文件传递。
线协议最多 96 MB，最多八个排队请求。
`--timeout` 包含排队、启动和执行；Studio 多阶段构建/恢复共用一个提交时间。
取消请求有自己的 id、`operation: "cancel"` 和 `target: ORIGINAL_ID`，确认与原请求取消响应分离。
id 必须是字符串或整数。EOF 排空已提交请求，SIGTERM/SIGINT 取消待处理工作并关闭子进程。
Studio 方法调用使用 `operation: "studio"`、method、args；`method: "cancel"` 停止 VM 会话，
传输层 cancel 则取消排队或执行中的请求，Bridge 测试覆盖两条路线。

## 增量源码分析

原生解析流按编译器、模块路径、精确文本缓存，最多 128 项、32 MB。未改模块经有界类型文件复用 token/节点。
`incremental: false` 关闭解析、IR 和完整产物复用。
每次检查都执行全局绑定与类型解析，再按变化源码和反向模块依赖图选择函数体。
接口签名排除函数体/位置，保守包含导出类型可能引用的私有声明。接口变化使传递依赖失效，函数体变化仅重查该模块。
产生构建产物时检查所有函数体。
全局解析失败不能复用旧成功，未变模块诊断保留并确定性排序。
测试比较缓存开关的结果/诊断以及传递依赖、删文件、路径归一化、取消、重启。

## CLI 与 Studio

CompilerClient 补充标准库，使用自举导入发现，并转换诊断位置。
项目 build/run/test、独立文本/PNG 编译运行调试、pack/render/unpack 均调用服务。
成功后以唯一临时文件发布，失败保留旧产物。
Studio 使用相同客户端进行检查、构建、IR、图片和时间记录恢复；编辑会取消旧检查，快照守卫阻止过期结果。
构建/恢复可取消，DAP 处理编译完成前的终止与编辑，仅产物成功后注册会话。
格式化、导航和可选空间编辑仍使用宿主工具，不属于自举 check/build/recovery 路径。

## 调试与恢复

debug-compile 产生字节码、源码/函数身份、参数/捕获/局部名和泛型实参；debug-pixels 产生区域与 AST 范围映射。
prepare_debug 不调用宿主前端，要求完整函数/模块覆盖，将指令范围映射到真实像素，不改变执行语义。
时间记录保存完整规范字节码哈希，恢复重新编译快照并要求字节码与语义源码相同，不能静默换编译器回放。
测试覆盖泛型/闭包帧、跨模块断点、像素映射、逆向执行和损坏记录。

image_source.pxl 验证 PNG、构造检查语义 AST，source_writer.pxl 恢复多文件文本，支持闭包、泛型、
名义类型、模式、集合和控制流；不读取隐藏完整源码。名称提示必须验证，碰撞或不安全路径使用生成身份，
导入相对恢复路径重建并重新检查。PNG 头为 0.9，有界连接分块；语义像素为 0.8，见[图片格式](formats/png.md)。
生成编译器回归禁止调用宿主前端/图片编解码器。

## 构建与验证

```sh
.venv/bin/python scripts/build_service_compiler.py --seed VERIFIED_STAGE3.json --output NEW_ARTIFACT_DIRECTORY
.venv/bin/python -m pixellang.compiler_rpc --compiler NEW_ARTIFACT_DIRECTORY/compiler.json
```

构建记录源码、种子和产物哈希并拒绝变化输入，但单次构建不证明三代固定点或完整 PNG 往返。
[分层验证](../contributing/verification.md)区分相关测试、合并、发布自举和包/编辑器验收。

## 共享 Studio 事务与精确缓存

debug-bundle 一次检查，共享程序与 IR 生成字节码、帧信息、映射像素及可选 IR/PNG。
PNG 从移除源码映射后的语义文档生成，保留验证过的名称，无需第二次前端调用。
Studio 请求可见 PNG，打开 Compiler · IR 面板时才序列化 IR；仅记录编译省略可选输出，恢复在同一事务请求图片。

成功 bundle 按编译器哈希、限制和完整输入缓存，只有 id/revision 不参与键。
files、entry、可选产物、scale 均参与，最多 16 项、32 MB 序列化结果。
调用者得到隔离值，失败或取消不缓存。reuseArtifacts: false 只跳过该层，incremental: false 跳过全部三层。
命中仍检查取消/期限，报告 artifactCacheHit、artifactCacheBytes 和零 VM 指令；函数体修改仍全面检查。

Studio inspect-ir 方法接收会话 ID，首次检查时编译不可变快照并在会话内保留 IR。
普通构建、单步和回放不触发；扩展合并待处理请求，编辑时取消并丢弃过期会话结果。
面板打开才请求，失败不随每次运行状态自动重试。它可能重新检查一次，但可复用兼容函数 IR，
请求间不保留已检查 AST。

## 跨修改 IR 缓存

compile、debug-compile、inspect-ir、debug-bundle 支持布尔 reuseIR，默认 true。
进程内保留一份成功快照，容量为 8 MB 与文件预算八分之一的较小值；允许容量小于 1 MB 时关闭，进程终止即丢弃。
源码戳包含精确模块文本及传递依赖接口。在复用前仍执行绑定、类型、函数体检查、特化和帧规划。
PixelLang 匹配身份并重定位函数/类型/模块，Python 仅管理有界存储与传输；非规范路径正常重新降低。

函数按需读取，重定位未改变体时保留原字节；旧/新文件名隔离避免编号变化导致覆盖。
过大函数不入缓存，成功且最终取消/期限检查通过后才原子发布；诊断、失败或取消不替换旧快照。
指标 irCacheHits 表示复用函数数，irCacheRelocated 表示在变化 ID 映射下处理的复用函数数，irCacheBytes 表示保留字节。
reuseIR: false 不清空旧快照，也不关闭解析缓存；精确 bundle 命中不执行 IR 工作，IR 命中记零。
缓存传输和首次填充有成本。对照时两组均设 reuseArtifacts: false，只切换 reuseIR；
见[性能测量](../contributing/performance.md)与[IR](../internals/ir.md)。
