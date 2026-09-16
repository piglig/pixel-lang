# 数据语义（语言 0.8）

[中文](data.md) · [English](../../../en/reference/language/data.md) · [文档首页](../../README.md)

绑定是名称到值的关联，对象是可被多个绑定引用的数据。普通赋值不隐式深复制，也不产生深不可变性。

| 操作 | 规则 |
| --- | --- |
| `let name = value` | 绑定不可重新赋值，所引用字段和元素可修改。 |
| `var name = value` | 可重新赋同一静态类型的值。 |
| 函数参数 | 绑定不可重新赋值，对象与调用者共享。 |
| 赋值与传参 | 复制标量值、共享对象引用；局部重新绑定不改变调用者绑定。 |
| 字段和下标更新 | 所有别名可观察修改，仍执行边界和类型检查。 |
| `collections.Copy(values)` | 创建新外层数组，元素共享；不会复制元素记录。 |
| 独立记录 | 显式构造新记录，并复制需要独立的嵌套对象。 |
| 标量相等 | 相同 int、float64、bool、string 按值比较，无数值转换。 |
| 对象相等 | 数组、映射、记录、枚举、JSON、函数不能直接判等；显式比较所需字段。 |
| 遍历 | 入口时建立外层浅快照，嵌套对象仍共享。 |
| 闭包 | 捕获绑定，共享可变绑定单元；每次声明和迭代建立新绑定。 |
| 可捕获失败 | 已产生的修改保留，catch 不回滚事务。 |
| 回放 | 恢复对象身份与修改，观察过去不会重复外部写入。 |

```pixellang
import "std/collections.pxl" as collections
record Item { amount: int }
fn change(item: Item) { item.amount = 9 }
fn main() {
    let original = [Item{amount: 1}]
    let copied = collections.Copy(original)
    change(copied[0])
    print(original[0].amount)
    copied[0] = Item{amount: 2}
    print(original[0].amount)
}
```

两次输出都是 9：元素 Item 最初共享，替换 copied 的元素不会替换 original 的元素。
自动深复制会隐藏成本，还需定义环和闭包策略；这不是赋值或 Copy 的含义。
结构相等同样需要递归身份和环规则，因此当前使用显式领域字段比较。
相关测试：`tests/test_data_semantics_v8.py`、闭包、集合回调和捕获单元测试。
