# 类型化 JSON

[简体中文](json.md) · [English](../../../en/reference/language/json.md) · [文档首页](../../README.md)

用 jsonDecode 将外部数据转换为已验证的语言值。结构不匹配通过 Result 返回，资源限制仍是执行边界。

## 规则与接口

`jsonDecode::[T](input)` 返回 `Result[T, Error]`，成功值经过静态类型检查。输入可为 string 或已解析的不可变 json。
这是使用泛型实参语法的受检查内建操作；动态结构仍可使用 `std/json.pxl` 检查。

```pixellang
record Item { title: string, counts: [int] }
effect fn main() {
    match jsonDecode::[Item](input()) {
        .Ok(item) => { print(item.title) },
        .Err(error) => {
            print(error.code)
            print(error.path)
        }
    }
}
```

## 目标类型

目标支持 int、有限 float64、bool、string、json、数组、类型化映射、Option、枚举和记录，
包括跨模块记录及有限输入的递归模式。检查记录可见性，必填字段不能缺失，未知字段失败。
Option 字段缺失或 null 变为 None；json 本身也接受 null。没有字符串到数值的隐式转换。
枚举对象与嵌套 Option 规则见[枚举](enums.md)。int 必须在数学上为整数且位于 int64 范围，精确的指数表示也合法。
结构化 Error 值描述可恢复的转换失败；函数值不能 JSON 编解码。

## 错误处理

转换失败返回 `.Err(error)`，成功返回 `.Ok(value)`。使用 `match` 处理结果，或在
返回兼容 Result 的函数内用 `?` 显式传播。错误路径可定位到 `$[0].amount` 等位置，
结构解析失败保留解析诊断。资源限制和取消会终止执行。

## 值与资源限制

解码后的记录和容器采用普通值语义。`let` 绑定只读；需要修改字段或元素时，
将成功值复制到本地 `var`。本地修改不会改变其他值或历史快照，不存在 `mut`、
`freeze` 或 `thaw` 权限形式。GC 中的部分分配保持为根，失败转换不会泄露半成品。
仍执行 JSON 深度／节点与 VM 堆限制。

解析和序列化均最多 1,000,000 字符、100,000 个值节点，根深度为零，最大深度 128。
标量、数组、对象各算一个节点；对象键只标记值，不额外计节点或深度，但必须是合法 Unicode 标量字符串。
`{"a":1,"b":2}` 有三个节点：一个对象和两个数字。读写使用相同计数规则。

## 交付与恢复

目标类型由可执行像素及 RESULT_DECODE 指令携带，不藏在元数据里。恢复重建类型化表达式，
时间回放重做确定性转换而无文件 I/O。测试覆盖独立载荷、精确数字、缺失/额外字段、类型、GC、图片和记录恢复。

## 继续阅读

- [完整文档目录](../../README.md)
- [语言总览](overview.md)
