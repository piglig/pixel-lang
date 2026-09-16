# 编译器架构

[中文](architecture.md) · [English](../../en/internals/architecture.md) · [文档首页](../README.md)

语言行为以[语言参考](../reference/language/overview.md)为准，本页描述实现。

## 共享编译路径

CLI 和 VS Code 调用 CompilerClient → 受监督的 CompilerWorker → CompilerService。
服务在 Python PixelVM 中运行固定且经过哈希验证的编译器字节码，不为每次请求重新自举。
`selfhost/` 的 PixelLang 编译器执行文本解析、模块图、绑定、类型解析、表达式检查、泛型/闭包特化、IR 与汇编。
文本直接生成字节码，不需要 PNG 往返。

PNG 由 PixelLang 图片和像素读取器重建 AST，再走相同检查与降低；不执行隐藏源码或内嵌字节码。
源码恢复从语义表示打印文本，图片导出编码经过检查的语义。
服务缓存解析流，按依赖接口选择检查函数体；构建检查所有体后，可复用兼容 IR，重定位由 PixelLang 完成。
Studio 共享字节码、调试映射和 PNG 的检查事务，IR 面板打开时才加载和保留 IR。
精确操作、失效和取消规则见[服务协议](../reference/compiler-service.md)。

## 运行时与编辑器

Python VM 运行编译器和用户字节码，管理堆与通用内建操作。
调试元数据和像素映射连接源码、栈帧与视图；时间记录绑定完整字节码哈希。
格式化、不完整源码导航和可选空间编辑保留宿主工具。
Python 文本/空间编译器用于引导和参考测试，不是默认应用编译路径；删模块前先阅读[Python 边界](python.md)。

## 源码空间与时间

可执行源码为二维，Studio 显示 Z 表示逻辑执行时间而非第三源码坐标。
IR/VM 独立于显示布局，完整三维源码尚未支持。

## 验证

[分层验证](../contributing/verification.md)区分相关、合并与发布门禁。
验证必须对应当前源码、运行时及编译器产物。
