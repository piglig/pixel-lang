# 使用 VS Code Studio

[简体中文](studio.md) · [English](../../en/guides/studio.md) · [文档首页](../README.md)

Studio 在 VS Code 中连接源码、空间视图和执行历史。它是本地扩展，不需要另起浏览器服务。


**本页内容**

- [安装与配置](#安装与配置)
- [编写程序](#编写程序)
- [构建、运行与导出](#构建运行与导出)
- [源码调试与历史](#源码调试与历史)
- [空间和依赖图](#空间和依赖图)
- [二维语义编辑](#二维语义编辑)
- [事件应用](#事件应用)
- [排查问题](#排查问题)

## 安装与配置

1. 完成[工具链安装](installation.md)，确认 `pixel doctor` 成功。
2. 在 VS Code 执行 **Extensions: Install from VSIX**，选择匹配平台的扩展包。
3. 打开项目，执行 **PixelLang: Select Python Interpreter**，选择已安装运行依赖的解释器。
4. 执行 **PixelLang: Check Environment**，在 PixelLang 输出面板查看结果。

扩展自带编译器和宿主代码；解释器仍需要 Python 3.11+ 与 Pillow 10–12。
可以设置 `pixellang.pythonPath`。项目以 `pixel.toml` 确定入口；独立文件可通过 `pixellang.entry` 固定入口。

从源码构建扩展前，先准备好本机编译器，再在仓库根运行：

```sh
npm --prefix vscode ci
npm --prefix vscode run package
```

打包不等于发布。平台包构建和安装验收见[验证指南](../contributing/verification.md)。

## 编写程序

打开 `.pxl` 文件即可使用诊断、补全、悬浮类型、签名帮助、大纲、定义、引用、重命名和格式化。
这些功能使用原生编译器的语法、类型和绑定信息。格式化处理当前未保存文档，无须导入文件存在；
重命名则重新检查项目引用，存在诊断或名称遮蔽冲突时拒绝修改。

项目相关操作见[项目指南](projects.md)：保存并测试、保存并构建 PNG、更新依赖锁以及选择单个测试。

## 构建、运行与导出

执行 **PixelLang: Open Studio**：

| 操作 | 结果 |
| --- | --- |
| Build | 编译当前缓冲区快照，准备调试产物 |
| Run / Step | 使用运行输入执行，或逐步推进 |
| Program image | 按需生成可执行图片 |
| Export PNG | 选择保存位置后生成并导出 PNG |
| Compiler · IR | 首次打开时请求中间表示 |
| Recover Project from PNG | 将图片恢复到空目录 |

普通 Build、单步与记录恢复不自动生成 PNG。源码或输入变化会使旧运行快照失效。
构建通知显示文件读取和等待时间，不显示虚构的完成百分比；两个阶段都可以取消。
输出面板记录文件收集、排队、编译与调试初始化耗时。

## 源码调试与历史

按 F5 启动 PixelLang 调试。支持断点、步入／步过／步出、暂停、逆向指令、调用栈和分页变量。
时间变化后旧变量引用失效；源文件缺失或已修改时显示匹配执行产物的只读源码。

launch 配置中的 `program` 选择入口，`input` 是 `input()` 的文本，`args` 是程序参数数组。
文件访问需要 `pixellang.readRoot`／`pixellang.writeRoot` 授权。Pause 可以继续，Cancel 终止当前运行并保留历史；
取消在执行批次边界生效，不能强行中断正在进行的宿主调用。

**Save timeline** 保存 `.pixeltime`；**PixelLang: Open Time Recording** 重新打开记录。
恢复会重新编译记录中的源码快照并核对字节码摘要，不依赖当前项目文件，也不重复文件写入。
精确格式见[文件类型](../reference/formats/files.md)。

## 空间和依赖图

执行 **PixelLang: Open Native Graph** 查看 flow 的命名计算、状态、子实例、`next` 和输出。
修改图中的表达式会写回同一份未保存源码；编辑器 Undo 可撤销。Add state 接受名称、类型和初值表达式。
语法不完整时先在文本中修复诊断。

调试暂停后，**Paused execution** 显示当前 flow 的输入、计算结果、旧状态和待提交值。
Space／Entity 的 **Space** 入口按页显示实体、坐标系、快照及邻域；复杂负载按需展开。
继续执行、修改源码或切换会话会清除过期调试值。

- **Orbit**、缩放和图布局只改变视图。
- **Source coordinates** 编辑具有直接字面量来源的坐标。
- **Move X/Y/Z** 沿指定轴拖动实体，松开时提交一次可撤销源码修改，Escape 取消。
- **Reveal expression** 定位计算得到的位置，不将表达式覆盖为常量。

坐标编辑影响该源码表达式的所有执行实例，需重启调试才能看到新的程序结果。
空间 Z 是坐标；**3D · Time** 中的显示 Z 是逻辑转移序号，两种视图不能混为一谈。

## 二维语义编辑

**2D · Edit** 将语义像素关联到源码。可修改字面量、操作符，或在同一语句列表移动相邻语句。
编译器验证多文件快照后，扩展应用一次 WorkspaceEdit。过期或非法编辑不写入；不提供任意涂色或完整三维源码雕刻。

## 事件应用

执行 **PixelLang: Open Native Application** 打开入口源码或 PNG，输入类型化事件、逐步推进、查看历史并保存应用日志。
**Debug event** 进入选中事件的源码调试。调度与记录规则见[状态机指南](state-machines.md)。

## 排查问题

| 现象 | 检查位置 |
| --- | --- |
| Python 或 Pillow 不可用 | Select Python Interpreter → Check Environment |
| 构建慢 | PixelLang 输出面板；区分文件收集、排队、编译与图片生成 |
| 坐标无法拖动 | 是否有直接字面量来源；视线是否几乎沿目标轴 |
| 修改后仍显示旧状态 | 重启调试；旧快照不会自动成为新运行 |
| 文件读取被拒绝 | 检查读写根目录和相对路径 |

构建和后台分析使用独立进程；增量与预算契约见[编译服务](../reference/compiler-service.md)。
