# VS Code Studio 工作站

[中文](studio.md) · [English](../../en/guides/studio.md) · [文档首页](../README.md)

Studio 是 VS Code 扩展，无需 HTTP 服务或外部浏览器。扩展自带编译器，Python stdio 服务负责编译、执行、图片和时间记录。

## 安装与编辑

在仓库根执行 uv sync --locked，然后安装本地 VSIX（Extensions → Install from VSIX）。
运行需要 Python 3.11+ 与 Pillow，优先使用工作区 .venv，再使用 python3（Windows 为 python），可设置 pixellang.pythonPath。
打开 .pxl 文件；具备实时诊断、补全、定义/引用、重命名和保留注释的缩进格式化。
项目使用 pixel.toml，独立文件可设置 pixellang.entry 固定入口。

## 构建与运行

执行 **PixelLang: Open Studio**。Build 编译当前缓冲快照并生成包含可达模块的 PNG，Run 使用输入执行，Step 推进一步。
源码或输入变化会使旧运行失效。Program image 展示真实可执行图片，Export PNG 导出，
**PixelLang: Recover Project from PNG** 恢复到空目录；不恢复注释和原排版。
Compiler · IR 面板首次打开才请求 IR，普通单步和回放不会重复生成它。
项目保存/测试/构建与锁操作见[项目指南](projects.md)。

## 空间编辑

2D · Edit 中选择模块、像素或 Selected semantics，查看语义并定位源码；文本选择同步高亮最小目标。
Set literal、Set operator 修改字面量或二元操作符，Move ↑/↓ 在同一语句列表移动相邻语句。
服务对精确快照进行完整多文件像素编译检查，扩展重新验证缓冲后应用一次 WorkspaceEdit，普通 Undo 可恢复。
失效或非法提议不写源码，不执行文件效果。其他编辑后刷新；记录快照仍绑定原运行。
不提供任意涂色或完整三维源码编辑。文件身份用规范路径，编辑复用用户打开 URI，避免符号链接重复缓冲。

## 时间与调试

3D · Time 的 X/Y 是模块局部源码位置，Z 是逻辑转移序号。拖动旋转、滚轮缩放、点击体素或时间滑块选择状态。
显示当前时间附近的有限窗口，模块分离；状态来自检查点与确定性回放，不由图形位置插值。
Save timeline 写 .pixeltime，**PixelLang: Open Time Recording** 可不依赖原项目重开。
记录保存源码、输入、预算、检查点间隔和取消位置，格式为 0.8。

F5 选择 PixelLang，可设置断点、步入/步过/步出、暂停、逆向指令、栈帧、具名局部、堆及操作数栈。
结束后保留停止态以便逆向，Stop 结束会话。变量按需分页，时间变化后旧引用失效。
缺失或变化的源码显示匹配产物的只读快照（含标准库）。
launch 可设 program、input、stopOnEntry；input 是 input() 读取的字符串。
文件 I/O 需 pixellang.readRoot/writeRoot 授权，无网络访问。
Pause 暂停自动推进，可继续；Cancel 终止运行但保留历史，在当前最多 2,000 指令的批次后生效，不强行中断宿主函数。

## 构建扩展

在仓库根执行：

```sh
npm --prefix vscode ci
npm --prefix vscode run package
npm --prefix vscode test
```

输出 pixellang-studio-0.9.0.vsix，打包不发布。测试使用固定 VS Code 1.137.0，可通过 PIXELLANG_VSCODE 指定本机可执行文件。
真实交互验证需要解锁桌面，详见[贡献指南](../contributing/README.md)。
