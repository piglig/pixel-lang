# 自举编译器接口

[中文](compiler-interfaces.md) · [English](../../en/internals/compiler-interfaces.md) · [文档首页](../README.md)

这些是实现层约束，语言规则见[参考](../reference/language/overview.md)。
字段定义以 [model.pxl](../../../selfhost/model.pxl)、[ir_state.pxl](../../../selfhost/ir_state.pxl) 及各模块导出类型为准。

## 源码、语法与身份

驱动传入相对 POSIX `.pxl` 路径到文本的映射，标准库以 std/ 纳入；解析器不暗查宿主文件系统。
相对导入基于当前模块，std/ 基于映射根。拒绝越界、绝对路径、反斜杠、NUL、归一化重复路径及超过 128 文件。
project.Build 按依赖先于使用者的顺序产生模块，保留解析 arena、导入目标/别名/节点、顶层声明名/类别/节点/可见性。
循环、缺失源、重复声明/别名和非法模块语句产生诊断；部分模块数据不能继续当成功产物。

节点索引模块局部，子节点先于父节点，父范围包含子范围；token 偏移以 Unicode 标量计数。
跨模块身份是模块 ID 与局部节点 ID，不只靠名称。Scope.parent 表示词法父，owner 是函数节点（模块为 -1）。
Symbol 保存声明模块/节点、作用域、类型和可见性，mutable 只表示重绑许可。
Reference 记录名称解析结果，Capture 记录函数与外部 Symbol，必须去重且确定性排序；类型与别名同样遵守可见性。

## 类型、实例与帧

项目级 TypeInfo 驻留 primitive、array、map、function、record、enum、type_parameter。
参数列表包含容器值类型、名义实参或函数参数与结果；名义身份包含声明模块与节点。
记录字段、枚举变体与载荷保留类型身份，泛型定义保留开放类型，实例保留声明与替换参数。
未解析的 -1 类型不能进入执行发射。

处理链为 BoundProject → ResolvedTypes → ExpressionCheck → InstancePlan → ClosurePlan。
schemas.Materialize 从具体根遍历结构参数、字段和载荷，visited 防止递归重复，首次发现顺序决定模式顺序。
先替换泛型字段，Option/Error 使用同一路径，结构深度最多 32、名义模式最多 256。
schemas.Build 从可达签名、具体局部/表达式类型、闭包签名及捕获播种；开放模板名不是运行时根，未使用局部槽仍贡献根。

frames.Build 先为具名实例，再为闭包分配函数 ID。FramePlan.entry 保留规划入口。
每帧保存来源、具体签名、所属实例与闭包索引；槽依次为参数、捕获、局部，以 Symbol ID 区分遮蔽。
捕获沿用原绑定 Symbol，类型必须具体，可变性独立于槽类别。
布局不分配运行单元；IR 发出 CAPTURE/CLOSURE 和每次声明/迭代的 BIND。
循环快照、游标和 match 主体等临时槽在降低时追加，总数不超过 65,536。

## IR、控制流与汇编

ir_state 的 Instruction 保存 op、类型化 JSON 参数、模块/节点和来源；Function 保存签名、槽数和指令；
Program 保存版本、模式、入口和函数。名义类型拼写为 @module:declaration 加具体参数，字符串运行时名为 str。
具体语义见[IR](ir.md)：保留来源、具体类型和符号常量，失败诊断使产物不可执行。
for 仅求值一次，ITER_SNAPSHOT 支持单/双绑定；continue 推进，break/continue 仅退出相应处理器。
match 保存主体一次，使用 ENUM_IS/ENUM_GET 并创建分支绑定，不匹配主体陷阱；JSON_DECODE 携带替换后的类型。

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
| image_transport、image_worker | 有界模块与大载荷传输 |
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
