# PixelLang — Product Requirements Document

**Version:** 0.1
**Status:** Draft
**Project Type:** Experimental Programming Language
**Language Paradigm:** Spatial Programming Language
**Initial Source Representation:** 2D RGBA Pixel Grid
**Future Source Representation:** 3D Voxel Space

---

# 1. 项目概述

## 1.1 项目名称

PixelLang

## 1.2 项目定位

PixelLang 是一种实验性的空间编程语言（Spatial Programming Language）。

与传统编程语言使用文本字符描述程序不同，PixelLang 使用：

* Pixel
* Color
* Position
* Region
* Spatial Relationship

共同构成程序语义。

PixelLang 的核心理念不是“使用 RGB 颜色代替文字代码”，而是：

> **程序本身可以存在于空间中。**

在 v0.1 中，程序使用二维 RGBA Pixel Grid 表示。

未来版本可以将相同的语言模型扩展到三维空间，使程序可以表现为 3D Voxel Structure。

---

# 2. 核心理念

PixelLang 的核心模型：

```text
Pixel
  ↓
Semantic Pixel
  ↓
Region
  ↓
Spatial Relationship
  ↓
Spatial AST
  ↓
Canonical AST
  ↓
IR
  ↓
Bytecode
  ↓
PixelVM
```

程序的语义由多个维度共同决定：

```text
Color
+
Position
+
Region
+
Spatial Relationship
+
Context
```

PixelLang 不应被设计成简单的：

```text
Color → Opcode
```

系统。

例如，不应简单规定：

```text
Red   = ADD
Blue  = SUB
Green = MUL
```

而应该让颜色提供局部语义信息，再结合空间结构产生更高层次的语言语义。

---

# 3. 产品目标

## 3.1 核心目标

PixelLang v0.1 的核心目标是验证：

> **二维图像是否可以作为一种真正的程序源码表示，而不是传统文本程序的可视化结果。**

用户应该能够：

1. 创建一个二维 Pixel Program。
2. 使用颜色表达语义信息。
3. 使用空间结构表达程序结构。
4. 使用 Region 表达组合语义。
5. 通过 Compiler 将 Pixel Program 转换为可执行程序。
6. 在 PixelVM 中运行程序。
7. 将编译错误映射回二维源码空间。

---

## 3.2 长期目标

PixelLang 的长期目标是探索：

> **空间是否可以成为与文本同等重要的程序表达媒介。**

未来希望支持：

* 2D Spatial Programming
* 3D Spatial Programming
* Spatial Debugging
* Program Visualization
* Spatial AST
* Visual Compiler
* Self-hosting
* Multiple Backends

---

# 4. 非目标

以下内容不属于 v0.1 的核心实现目标：

* 完整 3D Source
* Z 轴时间语义
* Native Machine Code
* 高性能编译器
* 完整 IDE
* GPU Programming
* 分布式执行
* 大规模标准库
* 生产环境级别的语言生态
* 完整 Self-hosting Compiler

这些能力可以作为未来版本的发展方向。

---

# 5. Pixel

## 5.1 Pixel 定义

Pixel 是 PixelLang 最基本的物理表示单元。

在 v0.1 中，一个 Pixel 至少包含：

```text
R
G
B
A
X
Y
```

其中：

* `R`：Red
* `G`：Green
* `B`：Blue
* `A`：Alpha
* `X`：水平坐标
* `Y`：垂直坐标

---

## 5.2 Pixel 的语言地位

PixelLang 不将 Pixel 定义为完整的语言语义单位。

Pixel 提供局部语义。

多个 Pixel 可以通过空间关系组合成为 Region。

因此：

```text
Pixel
  ↓
Local Semantic
  ↓
Region
  ↓
Composite Semantic
```

例如：

```text
[A] [ADD] [B]
```

单独的 Pixel 可以表示：

```text
Identifier A
Operator ADD
Identifier B
```

但三个 Pixel 的空间关系共同形成：

```text
Expression
A + B
```

因此：

> Pixel 是语言的基本空间语义单位，Region 是更高层次的组合语义单位。

---

# 6. Color

## 6.1 Color 的作用

颜色是 PixelLang 的重要语义载体之一。

颜色用于表达：

* Semantic Category
* Symbol
* Attribute
* Type
* Operator
* Literal
* Control Structure

具体定义由后续 Language Specification 和 Pixel Encoding Specification 决定。

---

## 6.2 Color 不直接等价于 Opcode

PixelLang 不采用简单的：

```text
Color = Opcode
```

模型。

例如：

```text
Red = ADD
Blue = SUB
```

不是语言的基础设计原则。

更合理的模型是：

```text
Color
+
Position
+
Region
+
Spatial Relationship
```

共同决定语义。

---

# 7. Spatial Programming

## 7.1 空间是语言的一部分

PixelLang 与传统文本语言的主要区别是：

> 空间关系本身可以参与程序语义。

例如：

```text
[A] [ADD] [B]
```

其语义不仅来自：

```text
A
ADD
B
```

还来自：

```text
A 与 ADD 的关系
ADD 与 B 的关系
三者的排列方向
三者之间的距离
三者所属的 Region
```

---

## 7.2 Spatial Relationship

PixelLang 至少需要支持以下空间关系：

* Adjacency
* Direction
* Distance
* Connectivity
* Containment
* Boundary
* Connection
* Ordering

具体语义由 `SPATIAL_SEMANTICS.md` 定义。

---

# 8. Region

## 8.1 Region 定义

Region 是多个具有语义关联的 Pixel 组成的空间结构。

Region 不简单等价于：

```text
所有相邻 Pixel
```

Region 的识别需要综合考虑：

```text
Connectivity
Semantic Compatibility
Boundary
Direction
Context
```

---

## 8.2 Region 的作用

Region 用于表达高于单个 Pixel 的语言结构。

例如：

```text
┌─────────────────┐
│ A   ADD   B     │
└─────────────────┘
```

可以形成：

```text
Expression
├── Operand A
├── Operator ADD
└── Operand B
```

Region 因此承担类似传统语言中：

* Expression
* Statement
* Block
* Function
* Scope

等结构的空间表达职责。

具体映射由 Language Specification 定义。

---

# 9. X / Y 空间语义

PixelLang v0.1 使用二维坐标：

```text
(X, Y)
```

空间位置具有默认语义。

## 9.1 X 轴

X 轴默认用于表达：

* 顺序
* 数据流
* 操作关系
* 执行顺序

## 9.2 Y 轴

Y 轴默认用于表达：

* 层级
* 结构
* Scope
* 分支
* 嵌套关系

---

## 9.3 Soft Spatial Semantics

X/Y 不是绝对语法规则。

默认空间语义可以被显式 Spatial Relationship 覆盖。

优先级：

```text
Explicit Spatial Relationship
        ↓
Spatial Position
        ↓
Visual Layout
```

因此：

> 位置提供默认语义，显式空间关系拥有更高语义优先级。

---

# 10. 2D Source Model

PixelLang v0.1 的源码空间为二维。

程序可以表示为：

```text
2D Pixel Grid
```

例如：

```text
┌───────────────────────┐
│                       │
│   A  ADD  B           │
│          │            │
│          C            │
│                       │
└───────────────────────┘
```

这不是代码的截图。

而是：

> **程序本身。**

Compiler 不通过 OCR 将图片转换为文本代码。

Compiler 直接解析 Pixel、颜色和空间关系。

---

# 11. 3D Source Model

## 11.1 未来扩展

PixelLang 的语言模型需要为未来三维空间扩展预留能力。

三维程序可以表示为：

```text
(X, Y, Z)
```

即：

```text
3D Voxel Space
```

程序可以表现为一个三维空间结构。

---

## 11.2 v0.1 不实现完整 3D

v0.1 的 Source Model 只实现：

```text
2D
```

3D 属于未来版本。

但是 v0.1 的数据结构、AST 和 Compiler Architecture 不应阻碍未来扩展到：

```text
2D → 3D
```

---

# 12. Z 轴与时间

Z 轴不在 PixelLang 的基础语言模型中定义为时间。

未来 3D Source 中：

```text
Z = Spatial Dimension
```

时间属于独立的 Temporal Dimension。

因此：

```text
Spatial Dimension
├── X
├── Y
└── Z

Temporal Dimension
└── T
```

两者必须保持概念上的独立。

---

# 13. Runtime Trace

Runtime Trace 与 Source Program 分离。

程序：

```text
Source
```

经过 Compiler 后：

```text
Executable Program
```

运行后产生：

```text
Runtime State
Runtime Trace
```

Runtime Trace 可以使用空间形式进行可视化，但不属于 Source Program。

因此：

```text
Source
  ↓
Compiler
  ↓
Executable Program
  ↓
Runtime
  ├── State
  └── Trace
```

Runtime Trace 不应默认写回 Source。

---

# 14. Spatial AST

PixelLang Compiler 首先生成 Spatial AST。

Spatial AST 保存：

* Semantic Structure
* Spatial Structure
* Position
* Region
* Connection
* Source Provenance

结构类似：

```text
Spatial AST
├── Semantic Structure
├── Spatial Structure
├── Region
├── Connection
└── Source Mapping
```

Spatial AST 是 PixelLang Compiler 的核心中间表示之一。

---

# 15. Canonical AST

Spatial AST 在语义分析之后生成 Canonical AST。

Canonical AST 主要用于：

* Semantic Analysis
* Type Checking
* Optimization
* IR Generation

Canonical AST 不应完全丢失源码空间信息。

至少需要保留：

```text
Source Provenance
```

以支持：

* Error Reporting
* Debugging
* Visualization
* Source Mapping
* Round-trip

---

# 16. Compiler Architecture

PixelLang Compiler 初步采用以下架构：

```text
Pixel Program
      ↓
Image / Pixel Loader
      ↓
RGBA Decoder
      ↓
Semantic Pixel Extraction
      ↓
Spatial Analysis
      ↓
Region Detection
      ↓
Spatial Relationship Analysis
      ↓
Spatial AST
      ↓
Semantic Analysis
      ↓
Canonical AST
      ↓
Type Checking
      ↓
IR
      ↓
Optimization
      ↓
Bytecode
      ↓
PixelVM
```

---

# 17. Compiler 与 Physical Representation 解耦

PixelLang 的语言语义与具体物理编码必须分离。

逻辑结构：

```text
PixelLang Semantic Model
          ↓
Physical Encoding
          ↓
RGBA
```

RGBA 是 v0.1 的主要 Physical Encoding。

未来可以支持其他 Physical Representation，而不需要重新设计语言核心。

---

# 18. `.pixel` 文件格式

`.pixel` 是 PixelLang 的原生程序文件格式。

`.pixel` 不应简单等价于 PNG。

建议结构：

```text
.pixel
├── Header
├── Version
├── Encoding Information
├── Dimensions
├── Spatial Data
├── Pixel Data
└── Optional Metadata
```

PNG 可以作为：

* Visualization Format
* Exchange Format
* Human-readable Representation

但 PNG 本身不是 PixelLang Language Definition。

---

# 19. Canonical Representation

PixelLang 应定义唯一的 Canonical Representation。

多个合法 Source Representation 可以表达相同语义：

```text
Source A ─┐
Source B ─┼──→ Same Program
Source C ─┘
```

经过：

```text
normalize()
```

之后生成：

```text
Canonical Representation
```

Canonical Representation 用于：

* Deterministic Build
* Reproducibility
* Version Control
* Diff
* Testing
* Compiler Validation
* Self-hosting

---

# 20. Language Features

v0.1 需要定义基础编程能力。

## 20.1 数据

* Literal
* Variable
* Identifier
* Type

## 20.2 表达式

* Arithmetic
* Comparison
* Logical Expression

## 20.3 控制流

* Condition
* Branch
* Loop

## 20.4 函数

* Function Definition
* Parameter
* Return
* Function Call

## 20.5 模块

* Module
* Import
* Export

## 20.6 错误

* Syntax Error
* Semantic Error
* Type Error
* Runtime Error

具体语言规则由 `LANGUAGE_SPEC.md` 定义。

---

# 21. IR

PixelLang 不直接从 Spatial AST 生成机器代码。

编译流程：

```text
Spatial AST
    ↓
Canonical AST
    ↓
IR
    ↓
Backend
```

IR 应与具体空间表示解耦。

未来可以支持：

```text
PixelLang
├── PixelVM
├── WASM
├── LLVM
└── Native Backend
```

---

# 22. PixelVM

v0.1 提供 PixelVM 作为主要执行环境。

PixelVM 至少需要定义：

* Program Counter
* Stack
* Memory
* Heap
* Call Stack
* Constants
* Instructions

基础指令预计包括：

```text
LOAD
STORE
ADD
SUB
MUL
DIV
CALL
RETURN
JUMP
JUMP_IF
```

具体指令集由 `BYTECODE_VM_SPEC.md` 定义。

---

# 23. Self-hosting

PixelLang 的长期目标之一是实现 Self-hosting。

初期：

```text
Host Language
      ↓
Bootstrap Compiler
      ↓
PixelLang Compiler
```

长期：

```text
PixelLang
    ↓
PixelLang Compiler
    ↓
PixelLang Compiler
```

最终目标：

> PixelLang Compiler 使用 PixelLang 自身实现。

---

# 24. Bootstrap Requirements

在实现 Self-hosting 之前，PixelLang 至少需要具备：

* Variables
* Types
* Functions
* Control Flow
* Data Structures
* String Processing
* Byte Processing
* File I/O
* Modules
* Error Handling

以及能够表达：

* Pixel Decoder
* Spatial Analyzer
* Parser
* Semantic Analyzer
* IR Generator
* Bytecode Generator

等 Compiler Components。

---

# 25. Reproducible Build

PixelLang 应支持确定性构建。

相同输入：

```text
Same Source
+
Same Compiler
+
Same Dependencies
```

应得到确定性的结果。

v0.1 至少保证：

```text
Semantic Reproducibility
```

未来可以进一步保证：

```text
Byte-level Reproducibility
```

---

# 26. Tooling

计划提供：

```text
pixelc
pixelrun
pixelinspect
pixelfmt
pixelrender
pixeldebug
```

v0.1 优先实现：

```text
pixelc
pixelrun
pixelinspect
```

---

# 27. Visual Tooling

PixelLang 的开发工具需要提供空间化操作能力。

至少包括：

* Pixel Editing
* Color Editing
* Region Editing
* Spatial Relationship Editing
* AST Visualization
* IR Visualization
* Runtime Visualization

未来 3D 版本需要支持：

* Voxel Editing
* 3D Navigation
* Layer Inspection
* Spatial Debugging

---

# 28. Debugging

错误必须能够映射回源码空间。

例如：

```text
Compilation Error

Region:
X: 42-57
Y: 18-24

Reason:
Invalid operand relationship
```

工具可以直接高亮对应的 Pixel / Region。

---

# 29. Round-trip

PixelLang 应支持：

```text
Source
  ↓
Decode
  ↓
Spatial AST
  ↓
Canonical AST
  ↓
Encode
  ↓
Canonical Source
```

目标：

```text
Semantic(Source)
==
Semantic(Canonical Source)
```

不要求所有输入图片都保持完全相同的原始字节。

---

# 30. Versioning

PixelLang 需要区分：

* Language Version
* Encoding Version
* File Format Version
* Compiler Version
* VM Version

语言语义不能因为 Compiler Version 改变而隐式改变。

Breaking Language Changes 必须升级语言版本。

---

# 31. Design Principles

## 31.1 Spatial First

空间结构必须能够参与程序语义。

## 31.2 Color Is Semantic Information

颜色是语义信息的重要组成部分，但不应简单等价于 Opcode。

## 31.3 Semantic / Physical Separation

语言语义与 RGBA Physical Encoding 必须解耦。

## 31.4 2D First

v0.1 优先实现完整的 2D Spatial Programming。

## 31.5 3D Compatible

架构需要为未来 3D Source 留出扩展空间。

## 31.6 Source / Runtime Separation

Source Program 与 Runtime State / Trace 必须保持独立。

## 31.7 Deterministic

编译过程应尽可能保持确定性。

## 31.8 Self-hosting

语言设计需要考虑未来 Self-hosting 的可行性。

---

# 32. MVP

v0.1 MVP 的核心目标：

> **证明一张二维图片可以作为真正的程序源码，并经过 Compiler 转换后执行。**

最小闭环：

```text
2D Image
   ↓
RGBA Decoder
   ↓
Semantic Pixel
   ↓
Region Detection
   ↓
Spatial AST
   ↓
Canonical AST
   ↓
IR
   ↓
Bytecode
   ↓
PixelVM
   ↓
Output
```

---

# 33. MVP Example

MVP 至少能够表达：

```text
10 + 20
```

例如：

```text
[10] [ADD] [20]
```

Compiler：

```text
Image
  ↓
PixelLang Compiler
  ↓
Program
  ↓
PixelVM
  ↓
30
```

关键要求：

```text
Source Image = Program
```

而不是：

```text
Source Image
    ↓
OCR
    ↓
Traditional Source Code
```

PixelLang 必须直接理解空间程序。

---

# 34. Success Criteria

PixelLang v0.1 成功需要满足以下条件：

### SC-01

能够使用二维 Pixel Grid 表达可执行程序。

### SC-02

Compiler 不依赖 OCR。

### SC-03

颜色参与程序语义。

### SC-04

空间结构参与程序语义。

### SC-05

存在 Spatial AST。

### SC-06

程序可以转换为 IR。

### SC-07

程序可以在 PixelVM 中执行。

### SC-08

编译错误能够映射回源码空间。

### SC-09

存在 Canonical Representation。

### SC-10

语言核心与 RGBA Physical Encoding 解耦。

### SC-11

架构能够支持未来 3D Spatial Program。

### SC-12

最终能够通过 Bootstrap Compiler 运行 PixelLang 程序。

---

# 35. Future Vision

PixelLang 的长期形态：

```text
                    PixelLang
                        │
                Spatial Program
                        │
          ┌─────────────┴─────────────┐
          │                           │
       2D Source                   3D Source
          │                           │
        Image                       Voxel
          │                           │
          └─────────────┬─────────────┘
                        │
                  Spatial AST
                        │
                  Canonical AST
                        │
                        IR
                        │
          ┌─────────────┼─────────────┐
          │             │             │
       PixelVM         WASM         Native
```

长期探索方向：

* Spatial Programming
* Visual Programming
* 3D Programming
* Temporal Programming
* Spatial Debugging
* Program Visualization
* Self-hosting
* Multiple Backends

---

# 36. Architecture Questions

以下问题不在 PRD 中最终冻结，需要通过 ADR 和后续 Specification 决定：

1. Pixel 的具体 RGBA Encoding
2. Region Detection Algorithm
3. Spatial Relationship Formal Semantics
4. X/Y Spatial Semantics 的精确定义
5. Variable Representation
6. Type System
7. Function Representation
8. Control Flow Representation
9. Module System
10. Error Model
11. `.pixel` File Format
12. PNG Interoperability
13. Canonical Representation
14. IR Design
15. PixelVM Instruction Set
16. Bootstrap Architecture
17. Self-hosting Strategy
18. Future 3D Representation
19. Temporal / Runtime Trace Model

这些问题必须通过 Architecture Decision Records（ADR）正式决定。

---

# 37. Documentation Architecture

PixelLang 后续文档结构建议：

```text
docs/
│
├── PRD.md
├── DECISIONS.md
│
├── LANGUAGE_SPEC.md
├── SPATIAL_SEMANTICS.md
├── PIXEL_ENCODING.md
├── FILE_FORMAT.md
│
├── AST_SPEC.md
├── COMPILER_ARCHITECTURE.md
├── IR_SPEC.md
├── BYTECODE_VM_SPEC.md
│
├── RUNTIME_SPEC.md
├── TOOLING_SPEC.md
│
├── BOOTSTRAP_SPEC.md
├── SELF_HOSTING_SPEC.md
│
├── CONFORMANCE_TESTS.md
└── ROADMAP.md
```

---

# 38. Development Process

PixelLang 不应直接从 PRD 进入大规模代码实现。

推荐开发流程：

```text
PRD v0.1
    ↓
Architecture Review
    ↓
ADR
    ↓
DECISIONS.md
    ↓
Language Specification
    ↓
Spatial Semantics Specification
    ↓
Pixel Encoding Specification
    ↓
Compiler Architecture
    ↓
AST / IR / VM Specification
    ↓
Implementation Plan
    ↓
MVP
    ↓
Testing
    ↓
Bootstrap
    ↓
Self-hosting
```

---

# 39. Core Statement

PixelLang v0.1 的核心命题是：

> **A Program Can Be a Space.**

程序不一定必须以文本形式存在。

程序也可以存在于：

```text
2D Image
```

或者未来的：

```text
3D Voxel Space
```

中。

PixelLang 的第一种物理表达形式是：

```text
2D RGBA Pixel Grid
```

但 PixelLang 的真正语言本体不是 RGB，也不是 PNG。

真正的语言本体是：

> **Spatial Program。**

RGB / RGBA 只是 PixelLang v0.1 用来承载 Spatial Program 的第一种 Physical Encoding。
