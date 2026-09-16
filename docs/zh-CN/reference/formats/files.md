# 源码、可执行文件与时间记录

[中文](files.md) · [English](../../../en/reference/formats/files.md) · [文档首页](../../README.md)

`.pixel` 是 UTF-8 JSON，magic 为 PIXELLANG，版本为
`{language:"0.8",encoding:"0.8",format:"0.8"}`，encoding 为 rgba8。
包含正的二维 dimensions、稀疏 position/RGBA pixels 列表、spatial.links 和可选 metadata。
坐标唯一、非负且不越界，最多一百万逻辑单元，原生文件最多 64 MB。
连接为 next/child 端点或 region 自标记。

可选 0.8 bundle 包含入口文件名和编号模块。元数据可以保留名称和文本范围，不能决定执行值、可变性或入口。
入口关键字编码在像素中。裸表达式不是可执行模块，必须放在入口函数内。

原始 PNG 使用精确 RGBA，透明单元为空；可选 pixellang 附加元数据承载连接，移除可能改变关系。
交付图片使用[可执行容器](png.md)，把模块与关系存在物理像素中，移除 PNG 附加元数据仍能恢复。
有损编辑和缩放均不属于合法传输。

`.pxb` 是 magic 为 PIXELVM 的 JSON，language/VM 为 0.8，compiler 信息版本为 0.8.0。
`.pixeltime` 是独立的 0.8 时间记录，包含源码、输入、执行长度/游标、预算、文件效果、取消位置和可选验证源码快照。
记录不修改源码，回放不重复外部写入。各读取器验证支持的版本；PNG 容器的版本支持见其格式说明。
