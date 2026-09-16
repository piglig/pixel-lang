# 命令行工具

[中文](cli.md) · [English](../../en/guides/cli.md) · [文档首页](../README.md)

仓库开发使用 uv sync --locked，或用 pip 安装构建的 wheel。命令有 python -m pixellang 对应入口。

| 命令 | 用途 |
| --- | --- |
| pixel init [目录] | 创建包含示例与测试的项目；目标目录必须为空 |
| pixel doctor [--json] | 检查 Python、宿主、Pillow PNG 编解码与编译器校验和 |
| pixel lock/test/build/run | 清单项目依赖、测试、构建和运行 |
| pixelc | 编译源码或图片 |
| pixelrun | 运行源码、图片或字节码 |
| pixelinspect | 检查 spatial_ast、canonical_ast、ir、bytecode |
| pixelfmt | 文本格式化 |
| pixelrender / pixelpack | 生成图片 |
| pixelunpack | 从交付 PNG 恢复项目 |
| pixeldebug | 调试工具入口 |
| pixelcompiler | JSON-lines 编译服务 |

各命令 --help 列出参数。运行输入为独立字符串/文件；数据文件访问需要显式读写根授权。
错误写 stderr 并返回非零，支持的 --json 输出结构化诊断。
[项目指南](projects.md)说明 pixel.toml、依赖锁与测试。
Studio 唯一宿主是 VS Code，无 pixelstudio 命令或浏览器服务；见[工作站](studio.md)。
