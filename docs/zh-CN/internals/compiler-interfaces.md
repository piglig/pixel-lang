# 编译器接口

[简体中文](compiler-interfaces.md) · [English](../../en/internals/compiler-interfaces.md) · [文档首页](../README.md)

本页按编译阶段描述模块之间的数据契约。修改接口时同步生产者、消费者、来源映射和差分验证。


**本页内容**

- [规则与接口](#规则与接口)
- [源码、语法与身份](#源码语法与身份)
- [类型、实例与帧](#类型实例与帧)
- [IR、控制流与汇编](#ir控制流与汇编)
- [像素与图片路径](#像素与图片路径)
- [共享可执行代码](#共享可执行代码)
- [编译器传输与复用](#编译器传输与复用)
- [常驻会话核心](#常驻会话核心)
- [继续阅读](#继续阅读)

## 规则与接口

这些是实现层约束，语言规则见[参考](../reference/language/overview.md)。
字段定义以 [model.pxl](../../../selfhost/model.pxl)、[ir_state.pxl](../../../selfhost/ir_state.pxl) 及各模块导出类型为准。

## 源码、语法与身份

驱动传入相对 POSIX `.pxl` 路径到文本的映射，标准库以 std/ 纳入；解析器不暗查宿主文件系统。
相对导入基于当前模块，std/ 基于映射根。拒绝越界、绝对路径、反斜杠、NUL、归一化重复路径及超过 128 文件。
project.Build 按依赖先于使用者的顺序产生模块，保留解析 arena、导入目标/别名/节点、顶层声明名/类别/节点/可见性。
循环、缺失源、重复声明/别名和非法模块语句产生诊断；部分模块数据不能继续当成功产物。

`Project.entryName` 指定最后一个模块中的顶层入口声明。文本项目选择 `main`；
像素项目保留被标记为入口的声明，实例化直接使用该名称，不插入 AST 包装函数。
因此像素规范化不会累积入口函数。恢复为可编辑文本时，源码生成器提供调用原像素入口的
`main`，保留原标识符的引用与词法遮蔽关系。

节点索引模块局部，子节点先于父节点，父范围包含子范围；token 偏移以 Unicode 标量计数。
跨模块身份是模块 ID 与局部节点 ID，不只靠名称。Scope.parent 表示词法父，owner 是函数节点（模块为 -1）。
Symbol 保存声明模块/节点、作用域、类型和可见性，mutable 表示绑定可写，包含本地值的嵌套更新。
Reference 记录名称解析结果，Capture 记录函数与外部 Symbol，必须去重且确定性排序；类型与别名同样遵守可见性。

## 类型、实例与帧

项目级 TypeInfo 驻留 primitive、array、map、function、record、enum、type_parameter。
参数列表包含容器值类型、名义实参或函数参数与结果；名义身份包含声明模块与节点。
记录字段、枚举变体与载荷保留类型身份，泛型定义保留开放类型，实例保留声明与替换参数。
未解析的 -1 类型不能进入执行发射。

处理链为 BoundProject → ResolvedTypes → ExpressionCheck → InstancePlan → ClosurePlan。
`ResolvedTypes` 是类型驻留数据的唯一权威对象；解析阶段的 `ResolutionIndex` 只保存
只读符号查找索引，不复制类型表。
`types.Prepare` 准备名称环境与基本类型；`types.ResolveDeclaration` 解析单个声明的注解、签名与显式绑定；
`types.Finish` 在所有名义类型结构就绪后验证约束。`types.Analyze` 组合这些阶段完成全量分析。
常驻 `type_queries` 按声明缓存结果，使用稳定语法身份与独立类型描述，恢复时重建当前类型表编号。
未变化文件跳过结构比较；声明修改或类型名解析目标变化时重新解析。所有类型结构恢复后仍统一验证约束，
包括未使用声明中的注解；失败请求不发布新快照。声明与表达式查询共享源码身份索引，反向位置仅为旧缓存实际引用的身份分配。
泛型特化与全局约束仍有全项目阶段；帧规划使用独立声明查询。类型替换与查询直接使用同一类型表。
`inference.Context` 仅保存参数、候选实参、冲突和待检查投影，采用普通值语义；
约束收集显式使用 `inout`，复制上下文后可独立推导。Preview／Complete 只读上下文，
类型表由调用者单独传入，新增的实例化类型仍保存在该表中。
类型表、检查状态和 IR 状态均遵循值语义；写回由显式访问授权。

schemas.Materialize 从具体根遍历结构参数、字段和载荷，visited 防止递归重复，首次发现顺序决定模式顺序。
先替换泛型字段，Option/Error 使用同一路径，结构深度最多 32、名义模式最多 256。
schemas.Build 从可达签名、具体局部/表达式类型、闭包签名及捕获播种；开放模板名不是运行时根，未使用局部槽仍贡献根。

frames.Build 先为具名实例，再为闭包分配函数 ID。FramePlan.entry 保留规划入口。
每帧保存来源、具体签名、所属实例与闭包索引；槽依次为参数、捕获、局部，以 Symbol ID 区分遮蔽。
捕获沿用原绑定 Symbol，类型必须具体，可变性独立于槽类别。
布局不分配运行单元；IR 发出 CAPTURE_VALUE/CLOSURE 和每次声明/迭代的 BIND。
循环快照、游标和 match 主体等临时槽在降低时追加，总数不超过 65,536。

## IR、控制流与汇编

ir_state 的 Instruction 保存 op、类型化 JSON 参数、模块/节点和来源；Function 保存签名、槽数和指令；
Program 保存版本、模式、入口和函数。名义类型拼写为 @module:declaration 加具体参数，字符串运行时名为 str。
具体语义见[IR](ir.md)：保留来源、具体类型和符号常量，失败诊断使产物不可执行。
for 仅求值一次，ITER_SNAPSHOT 支持单/双绑定；continue 推进，break/continue 跳转到所属循环的目标，并清理离开的访问。
match 保存主体一次，使用 ENUM_IS/ENUM_GET 并创建分支绑定，不匹配主体陷阱；RESULT_DECODE 携带替换后的结果类型。

assembler.Assemble 解析每函数 LABEL、去除标签后计算跳转，按类型与规范 JSON 值驻留 CONST，区分浮点正负零。
重复/未知标签和越界目标产生来源诊断；汇编不能替代 VM 模式及操作码验证。
字节码流协议分块传递完整产物，不以摘要代替指令。驱动操作以 main.pxl 为准，公开服务见[协议](../reference/compiler-service.md)。

## 像素与图片路径

| 模块 | 职责 |
| --- | --- |
| literal_pixels、pixel_tokens | 字面量字节、版本化类别表与受限标识符 |
| pixel_ast_state、pixel_ast_expr、pixel_ast | 从检查后的类型和绑定发射语义区域、名称、模块 |
| spatial_layout、picture_layout | 逻辑区域及物理模块布局，保存顺序/父边，限制尺寸 |
| pixel_decode、pixel_regions、pixel_reader、pixel_expressions、pixel_statements、pixel_types | 验证颜色、区域、类型与语法，重建带来源的节点 |
| pixel_restore | 将独立 lambda_body 在词法引用位置恢复一次，拒绝缺失/重复/环/未引用体，重建后序节点 arena |
| pixel_project、pixel_document | 验证模块身份、bundle 和模式；检测依赖环、入口，连接像素程序到公共检查链 |
| png、picture、picture_reader | PNG 二进制、物理头/布局、调色板与模块恢复 |
| image_transport | 有界模块与大载荷传输 |
| image_source、source_writer | 从受检查图片恢复可编辑源码 |

像素项目根模块为 0，依赖键为 1..65535 的规范十进制 ID，最多 127 个依赖。
入口保持独立身份，不通过全局名称替换实现。来源保留精确空间点，不误当文本偏移。
原生 .pixel 可省略 metadata/links 并使用空值默认，但显式 null 非法；bundle 版本必须受支持。
元数据不携带可执行文本、AST 或字节码。图片连接分块、布局和边界见[PNG 格式](../reference/formats/png.md)。

PNG 写入器输出 8 位 RGBA，无附加块；读取器支持非隔行 8 位 RGB/RGBA、五种滤波，
检查签名、块类型/保留位、CRC、顺序、连续 IDAT、IEND 和尺寸。拒绝未知关键块、重复调色板及不支持的 tRNS/颜色/隔行格式。
不支持调色板、灰度、16 位和 Adam7，附加块只校验 CRC 后忽略。
crc32、zlibCompress、zlibDecompress 是通用原语；解压有显式输出限制，拒绝损坏、截断、尾随和拼接流。
readBytes/writeBytes 使用授权根、相对路径、不跟随符号链接及原子写替换，单文件最多 6400 万字节。
效果记录校验请求摘要、读取限制和写入字节，回放不访问文件系统；堆、步数与效果预算继续有效。

`zlibCompress(bytes)` 同步读取整数数组，返回 `Result[[int], Error]`，成功值为独立的压缩字节。
非法字节返回错误码为 `bytes.range` 的 Err，输入保持不变。PNG 编码器使用此受检查的边界，
不再为压缩重复扫描整张画布。
`zlibDecompress(bytes, limit)` 返回 `Result[[int], Error]`，
显式类型写法为 `zlibDecompress::[[int]](bytes, limit)`。字节数组与其他容器共用
`let`／`var` 和 `inout` 规则，不提供可变类型包装或冻结与解冻转换。
完整验证通过后才发布成功值。图片恢复通过 `DecodeRaster` 保留行标记、原地解除滤波，
零滤波行无需复制像素。普通 `Decode` 接口会生成 RGBA 数据，因此需要额外工作空间。
当前产物与完整编译器 PNG 验收范围见[重设计记录](design.md)；
[早期迁移验收](value-storage.md)不能证明更新后的源码快照。

## 共享可执行代码

宿主 `CodeImage` 将字节码复制为只读对象图并验证一次。多个 VM 可以共享该代码图，
但堆、栈、输入输出、flow 状态和执行游标仍属于各自实例。修改调用方原始字典不会
改变已准备的代码。历史回放与单事件调试复用该对象，避免每次重建 VM 都复制和
验证整份指令及调试信息。直接传入字典的 VM 仍执行隔离和验证。

这是宿主内部的执行资源，不是 PixelLang 的不可变数据类型，也不是隔离恶意 Python
代码的安全边界。代码准备成本与程序执行成本需分别测量。

原生可执行文件的源码映射使用 `pixellang-native-source-map/2`。原生函数编号索引 `{name, instructionCount}` 条目，PC 索引 SHA-256 绑定字节码中的对应函数。指令和来源位置从该字节码读取，不在映射中重复保存。加载器拒绝旧映射以及函数身份或长度不一致的映射，避免启动时分配第二份完整指令对象图。

## 编译器传输与复用

字节码流协议 2 将源码位置按模块／节点身份去重，每个位置只发送一次。指令携带
已经发送的位置表整数索引；结束记录同时校验位置、指令与声明数量。宿主拒绝缺失、
乱序和非法引用，恢复完整字节码后仍执行验证。共享的源码位置一直保留到调试映像构造。
不再接受旧版字节码流协议。

受监督的工作进程通过私有二进制响应文件保留字节与对象共享关系。受限 pickle 读取器
禁止解析 Python 全局对象／类、持久化引用和尾随数据。这是临时内部 IPC，不能用作外部
产物格式；公开编译服务与 Studio 消息仍为 JSON。输出限额、取消、截止时间与进程终止
检查继续生效。

解析模块由原生会话持有。服务不再缓存编码后的解析分块，也不为参考执行写出解析临时文件；
参考 VM 请求直接消费完整源码。代码生成仍在每项目中只建立一次函数签名参数索引。

## 常驻会话核心

`selfhost/compiler_session.pxl` 持有解析模块以及绑定、声明类型、函数语义、帧和 IR 查询快照。
只有成功请求发布新快照；诊断失败保留上次成功状态，容量淘汰只影响加速。
复用时使用稳定声明／子节点路径，将符号、类型、指令与源码范围重定位到当前项目。
flow 的 IR、初始化函数和图元数据共同复用。全局索引、约束与特化仍有全项目阶段。
查询依赖、容量、失效和统计的权威定义见[编译服务](../reference/compiler-service.md)。

### 原生会话传输

`build_native(..., session={initialize, advance})` 绑定两个字节码函数：初始化返回拥有所有权的状态值，
推进接收并返回同一类型；源映射校验这一契约。`NativeSessionExecution` 使用带长度、请求编号和
独立预算的帧传输。成功响应发布状态；取消、超时和执行故障回收原生进程，后续请求重新初始化。
CompilerService 按产物声明选择执行器，工作进程退出时关闭它。

语义及 IR 快照不使用磁盘序列化，也不接受宿主重复构造的依赖图。
`incremental: false` 是全新编译对照。测试比较连续编辑后的诊断与字节码，覆盖契约变化、失败恢复、
名义类型及源码位置重定位和容量淘汰；安装与性能要求见[分层验证](../contributing/verification.md)。

## 继续阅读

- [完整文档目录](../README.md)
- [编译器架构](architecture.md)
