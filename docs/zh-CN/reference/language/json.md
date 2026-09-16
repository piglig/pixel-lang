# 类型化 JSON 解码

[中文](json.md) · [English](../../../en/reference/language/json.md) · [文档首页](../../README.md)

`jsonDecode[T](input)` 返回静态检查后的 T。输入可为 string 或已解析的不可变 json。
这是使用泛型实参语法的受检查内建操作；动态结构仍可使用 `std/json.pxl` 检查。

```pixel
record Item { title: string, counts: [int] }
fn main() {
    try {
        let item = jsonDecode[Item](input())
        print(item.title)
    } catch error {
        print(error.kind)
        print(error.message)
    }
}
```

目标支持 int、有限 float64、bool、string、json、数组、字符串键映射、Option、枚举和记录，
包括跨模块记录及有限输入的递归模式。检查记录可见性，必填字段不能缺失，未知字段失败。
Option 字段缺失或 null 变为 None；json 本身也接受 null。没有字符串到数值的隐式转换。
枚举对象与嵌套 Option 规则见[枚举](enums.md)。int 必须在数学上为整数且位于 int64 范围，精确的指数表示也合法。
Error 保留给运行时捕获的数据失败；函数值不能 JSON 编解码。

转换失败可捕获为 Error，消息标识 `$[0].amount` 等路径；结构解析失败保留解析诊断。
转换后的集合为可变副本，json 字段保留不可变子树。GC 中的部分分配保持为根，失败转换不会泄露半成品。
仍执行 JSON 深度/节点与 VM 堆限制。

解析和序列化均最多 1,000,000 字符、100,000 个值节点，根深度为零，最大深度 128。
标量、数组、对象各算一个节点；对象键只标记值，不额外计节点或深度，但必须是合法 Unicode 标量字符串。
`{"a":1,"b":2}` 有三个节点：一个对象和两个数字。读写使用相同计数规则。

目标类型由可执行像素及 JSON_DECODE 指令携带，不藏在元数据里。恢复重建类型化表达式，
时间回放重做确定性转换而无文件 I/O。测试覆盖独立载荷、精确数字、缺失/额外字段、类型、GC、图片和记录恢复。
