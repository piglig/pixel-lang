# 编译服务协议

[简体中文](compiler-service.md) · [English](../../en/reference/compiler-service.md) · [文档首页](../README.md)

协议版本为 1。CLI 和 Studio 使用相同请求、取消及快照规则；本页面向服务客户端和工具开发者。


**本页内容**

- [请求与结果](#请求与结果)
- [监督、取消和限制](#监督取消和限制)
- [增量源码分析](#增量源码分析)
- [CLI 与 Studio](#cli-与-studio)
- [调试与恢复](#调试与恢复)
- [构建与验证](#构建与验证)
- [共享 Studio 事务与精确缓存](#共享-studio-事务与精确缓存)
- [常驻 IR 复用](#常驻-ir-复用)
- [原生编辑查询](#原生编辑查询)
- [继续阅读](#继续阅读)

## 请求与结果

请求含 version: 1、id、revision、operation 及操作输入。id 关联请求，revision 标识源码快照；
响应回传两者及编译器 SHA-256。编辑器丢弃 revision 或源码与当前缓冲区不符的结果。

| 操作 | 输入 | 成功结果 |
| --- | --- | --- |
| check | files, entry | 结构化诊断，无产物 |
| compile | files, entry, 可选 entryFunction | bytecode |
| pixels | files, entry | 语义文档 |
| export-png | files, entry, scale | PNG 字节 |
| compile-png | PNG 字节 | PixelLang 恢复的字节码 |
| compile-pixels | document、可选 entry（诊断名称） | PixelLang 解码并编译语义像素文档得到的字节码 |
| recover-pixels | document、可选 entry（诊断名称） | 可编辑源码项目 files、entry |
| export-pixel-png | document、scale | 原生验证后的 PNG 字节 |
| normalize-pixels | document | 原生类型检查后的规范语义文档 |
| recover-png | PNG 字节 | 可编辑 files、entry |
| module-info | files, entry | imports、tests（name、valid）、hasMain |
| format | files, entry | 原生语法格式化；返回 text，不解析导入依赖 |
| debug-bundle | files, entry, includeIR=false, includePNG=false, scale | 字节码、调试元数据、映射文档，可选 IR/PNG |
| debug-compile | files, entry | 字节码和帧信息 |
| debug-pixels | files, entry | 带源码映射的语义文档 |
| export-debug-png | files, entry, scale | 带文件/符号名、不带源码映射的 PNG |
| inspect-ir | files, entry | 自举编译器 IR |
| native-graphs | files, entry | 原生图、端口、坐标表达式和源码范围 projection |
| editor-index | files, entry, 可选 editorRoots | 原生编辑索引 index；允许携带部分语法的诊断 |

进程内 PNG 为 bytes，JSON-lines 传输为 `{"$bytes":"BASE64"}`；含保留传输键的字典会转义，避免与程序 JSON 混淆。
响应 status 为 ok、diagnostics、error、cancelled 或 timeout，含 diagnostics、可选 result/error 及 metrics。
诊断保留模块、阶段、Unicode 标量偏移和从一开始的行列；取消和超时代码为 service.cancelled、service.timeout。
指标记录耗时、指令数、解析模块数、命中、常驻缓存节点数和精确产物缓存字节。
编译请求不运行用户程序；编译输出位于隔离临时目录，仅成功后返回，执行和调试是独立操作。

`compile.entryFunction` 选择入口模块中的顶层普通函数，省略时选择 `main`。
所选函数必须无泛型、无参数并返回 `unit`。编译器在实例化阶段选择入口，
不拼接源码；其他服务操作拒绝此选项。`module-info` 发现顶层 `fn test_*`
声明（包括导出函数），排除嵌套函数。签名标记只来自语法检查，编译时仍执行完整类型检查。

## 监督、取消和限制

`python -m pixellang.compiler_rpc`（安装命令 pixelcompiler）维护持久子进程。
`--compiler PATH` 显式选择开发产物，否则必须使用安装编译器及清单，不隐式回退到 Python 前端。
每个子进程是一条顺序执行通道。参考 VM 请求恢复空检查点；原生会话跨成功请求持有解析、语义和 IR 缓存。
取消先协作通知，最多等待 100 ms 且不超过原截止时间；已取消响应即使成功也会丢弃。
监督子进程可以继续使用，但被中断的原生会话会清除编译器缓存。启动取消、超时或无响应会强制终止。
正常关闭由工作进程先回收原生子进程，再退出；不再向已正常退出的进程组发送信号。
控制管道只传小消息，大响应通过临时文件传递。

默认 120 秒、5 亿指令、6400 万堆项、200 万堆对象、6400 万输出/文件字节；可配置项均有有限上界。
VM 一百万字符输入和普通 JSON 预算不变，类型化解析数据通过有界分块文件传递。
线协议最多 96 MB，最多八个排队请求。
`--timeout` 包含排队、启动和执行；Studio 多阶段构建/恢复共用一个提交时间。
取消请求有自己的 id、`operation: "cancel"` 和 `target: ORIGINAL_ID`，确认与原请求取消响应分离。
id 必须是字符串或整数。EOF 排空已提交请求，SIGTERM/SIGINT 取消待处理工作并关闭子进程。
Studio 方法调用使用 `operation: "studio"`、method、args；`method: "cancel"` 停止 VM 会话，
传输层 cancel 则取消排队或执行中的请求，Bridge 测试覆盖两条路线。

## 增量源码分析

增量解析、函数检查与 IR 复用统一由原生编译器会话管理。Python 服务不再保留解析模块、
计算语义失效或拼接历史诊断。参考 VM 和非常驻执行每次检查完整输入，作为独立的全新编译对照。
`incremental: false` 关闭会话复用与精确产物复用。常驻服务使用临时原生进程执行这类请求，成功或失败后均关闭，原增量进程与快照保留。旧缓存不占用全量编译进程的资源预算；两个进程可能同时存在，因此宿主总内存可能超过单进程预算。模块级声明表仍统一建立，声明体的绑定查询按实际名称依赖复用，
局部作用域、引用和闭包捕获会重定位到当前节点与符号。新增同名声明、可见性或导入目标变化使相关查询失效。声明类型结果按稳定身份复用并重定位；所有类型结构恢复后仍统一验证跨声明约束；
通过语义依赖与栈帧契约验证后，可复用成功的函数检查及其 IR。flow 语义查询保留类型化计划、
初值、子实例签名和表达式结果，复用时按稳定身份重定位节点、符号与类型。依赖契约变化会使查询失效。
失败检查不会发布部分语义快照。测试对照编辑、传递依赖、删文件、路径归一化、取消和重启
前后的完整结果与诊断。

## CLI 与 Studio

CompilerClient 补充标准库，使用自举导入发现，并转换诊断位置。
项目 build/run/test、独立文本/PNG 编译运行调试、pack/render/unpack 均调用服务。
成功后以唯一临时文件发布，失败保留旧产物。
Studio 使用相同客户端进行检查、构建、IR、图片和时间记录恢复；编辑会取消旧检查，快照守卫阻止过期结果。
构建/恢复可取消，DAP 处理编译完成前的终止与编辑，仅产物成功后注册会话。
格式化与语言导航使用自举编译器返回的 token、AST、作用域、绑定和类型信息。
宿主只投影编辑器结果与文本编辑，不重新解析或推断语言类型。原生图与坐标编辑
同样使用自举 AST 范围；原有二维语义编辑为单独入口。

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
此批量操作用于显式的完整产物检查。Studio 构建及录像恢复使用 debug-compile，文本位置和帧信息不依赖像素布局。
打开执行空间视图时才请求 debug-pixels；查看或导出图片时请求 export-debug-png；打开 IR 面板时请求 inspect-ir。
空间投影独立于执行字节码，不改变录像哈希或执行历史。录像格式 1.0 保存源码快照和执行身份，不保存生成的像素文档。
文本调试产物与完整 bundle 均可使用下述精确缓存。

成功 bundle 按编译器哈希、限制和完整输入缓存，只有 id/revision 不参与键。
files、entry、可选产物、scale 均参与，最多 16 项、32 MB 序列化结果。
调用者得到隔离值，失败或取消不缓存。reuseArtifacts: false 只跳过该层，incremental: false 跳过全部三层。
命中仍检查取消/期限，报告 artifactCacheHit、artifactCacheBytes 和零 VM 指令；函数体修改通过原生查询检查受影响函数。

Studio inspect-ir 方法接收会话 ID，首次检查时编译不可变快照并在会话内保留 IR。
普通构建、单步和回放不触发；扩展合并待处理请求，编辑时取消并丢弃过期会话结果。
面板打开才请求，失败不随每次运行状态自动重试。它可能重新检查一次，但可复用兼容函数 IR，
请求间不保留已检查 AST。

## 常驻 IR 复用

语义查询的函数体和依赖契约直接保留为结构值，通过内容相等比较，不经过 JSON 序列化。
语义、帧和 IR 快照中的结构项均计入缓存保留预算；容量不足时丢弃加速状态，不改变编译结果。
候选验收覆盖自举、PNG／文本恢复以及增量与全新编译的一致性。

开发编译器在常驻会话中直接保留已降低的函数值。`incremental` 默认 true，统一控制
解析、语义和 IR 复用；false 使用全新编译，不替换已有快照。分阶段缓存开关已删除。

编译器根据完整 token 值、位置和传递导入契约计算身份。普通函数的导入契约不包含函数体，
普通函数使用已检查函数的指纹、实际依赖契约和具体栈帧布局判断 IR 复用，
按声明身份和子节点路径重定位指令节点及当前源码范围，闭包使用同一规则。
flow 的语义查询可以独立复用；flow IR、初始化函数与图元数据作为一个整体缓存，
共同重定位节点、名义类型、调用目标及生成函数名。身份布局与声明 token 位置均未变化时可直接复用。
未保留语义查询的普通函数仍按模块失效。绑定和声明类型结果按声明复用，并重定位当前身份。
帧规划已接入函数查询：缓存以声明／闭包身份和泛型实参区分实例，
函数体或实际依赖契约变化时重算；复用时重定位符号和具体类型，不沿用旧编号。
帧结果与成功的 IR 快照一起发布和淘汰，计入保留预算。依赖契约在快照级共享，
不为每个函数重复保存映射。临时帧索引在 IR 构建前释放。
全局声明索引、类型约束验证和特化仍会执行，不能据此宣称完整函数级增量编译已经完成。
随附开发编译器已包含帧查询；专项、生成编译器及恢复产物回归、完整源码增量／全量对照、
完整 PNG 恢复与隔离安装均已验证。完整发布仍须满足分层验收要求。

快照直接包含函数与身份映射，不再序列化 IR 索引或函数文件，也不依赖宿主解析图。
复用前重定位类型、模块和调用目标，字符串常量不参与改写。降低失败不替换旧 IR 快照；
取消、执行故障或无效会话统计会回收进程及其全部缓存。容量淘汰只影响加速结果。

irCacheHits 统计复用函数，irCacheRelocated 统计在身份映射改变后复用的函数，
residentIRNodes 统计保留的 IR 结构数量，不能当作字节数。解析／语义与 IR 合计保留预算
为两百万个计数结构。完整产物命中时不执行 IR 工作；性能对照应关闭 reuseArtifacts，
比较 incremental 的开启与关闭，不能仅凭命中数判断提速。

### 声明查询统计

服务的 `metrics` 返回当前请求的查询计数：

| 字段 | 含义 |
| --- | --- |
| `bindingCacheHits` | 复用绑定结果的声明数 |
| `bindingCheckedDeclarations` | 重新绑定的声明数 |
| `typeCacheHits` | 复用类型分析结果的声明数 |
| `typeCheckedDeclarations` | 重新分析类型的声明数 |

这些计数针对常驻增量查询，每次请求重新计数。完整产物命中、请求在查询前失败，
或 `incremental: false` 使用非增量分析路径时返回零；零不表示没有执行其他编译工作。
计数按声明而非文件统计，包含函数、记录和枚举等声明，不能替代耗时与内存测量。

## 原生编辑查询

`native-graphs` 返回 `result.projection`，其中版本为 `pixellang-native-graphs/2`。
投影保留输入、命名表达式、持久子实例、状态、next/output 表达式、绑定依赖和坐标表达式；它是当前源码
的派生视图，不是另一份程序。诊断可能伴随部分投影，不能用作可执行产物。

`editor-index` 返回 `result.index`，包含原生解析节点、词法作用域、符号引用、
类型、调用签名、表达式和 flow 信息。语法未完成时仍返回 `status: ok` 与诊断，
供补全和导航使用；严格 `check`／`compile` 不会因此接受不完整程序。
`editorRoots` 最多列出 128 个已在 `files` 中提供的路径，默认覆盖传入文件。
客户端在注入标准库之前记录用户文件根，因此未使用的标准库不必进入编辑分析。
未从入口导入的用户模块仍可被索引；其依赖由原生模块解析器确定。

VM 0.9 索引将 `resolved.types` 中的声明模式与 `memberViews` 中的接收者权限分开。
`memberViews` 以十进制类型 ID 为键，包含 `storage`、`fields` 和 `variants`；`storage`
保留名义类型的声明身份，成员负载类型由原生检查器投影。成员查询应使用这些视图，
只读记录不能把声明为可变的字段暴露为可写类型；函数字段的调用签名保持不变。
缺失视图表示暂无成员信息，客户端不能自行重建权限。发布过程只处理已知类型，
不会递归展开可能无限增长的泛型模式。

这些结构引用同一编译器产物内的模块／节点／类型身份。跨编译请求不可缓存
单独节点身份；调用方必须同时核对完整源码快照和 revision。工作站据此执行
补全、签名、定义、引用、重命名、大纲、悬浮信息和格式化。重命名重新分析候选
源码并验证引用绑定，拒绝名称捕获与标准库写入。

## 继续阅读

- [完整文档目录](../README.md)
