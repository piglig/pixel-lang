# 值与修改权限

[简体中文](data.md) · [English](../../../en/reference/language/data.md) · [文档首页](../../README.md)

普通值的复制不会产生共享修改。用 let 表达只读绑定，用 var 表达本地修改，用 inout 表达显式写回。

## 规则与接口

普通记录、数组、映射和枚举采用值语义。`let` 保存只读值，`var` 允许修改本地值，
包括字段与嵌套容器。赋值、传参和返回不会产生可观察的共享修改。

| 写法 | 含义 |
| --- | --- |
| `let values = [1]` | 不能重新赋值或修改元素。 |
| `var values = [1]` | 可以重新赋值、修改元素，或调用 `values.append(2)`。 |
| `var copy = original` | 得到独立的值；修改 copy 不改变 original。 |
| `item with { count: 2 }` | 产生同类型的新记录值，未指定字段保留。 |
| 普通函数参数 | 只读；可赋给本地 var 后修改副本。 |
| `values: inout [int]` | 显式写回调用者变量，调用写作 `sort(&values)`。 |
| 闭包捕获 | 创建时按值捕获，捕获内容只读。 |
| 历史快照 | 后续修改不会改写旧值；回放不重复外部写入。 |

```pixellang
record Item { values: [int] }
effect fn main() {
    let original = Item{values: [1]}
    var edited = original
    edited.values.append(2)
    let replaced = original with { values: [3] }
    print(original.values) // [1]
    print(edited.values)   // [1, 2]
    print(replaced.values) // [3]
}
```

实现允许结构共享，值隔离不等于每次赋值都深复制，也不保证每次修改都无分配。
数组和映射的局部更新复制受影响的结构路径；资源预算及取消仍然生效。

普通值与运行身份分开：空间快照是值，实体、连接和活跃 flow 实例有显式身份。
复制流程句柄不会创建一个新实例；独立实例必须显式创建。

以上值语义已接入随附开发编译器和运行时。旧 `mut`、`Frozen` 类型包装以及
`freeze`、`thaw` 内建转换已删除；同名用户声明遵循普通名称解析规则。
可恢复失败使用 `Result` 与显式 `?` 传播，见[错误](errors.md)。

## 结构相等

`==` 与 `!=` 要求两侧静态类型相同。int、float64、bool、string、同坐标系的
Point3／Vec3、EntityId／LinkId 可比较；数组、类型化映射、记录和枚举在其所有元素或字段类型均可比较时可比较。
类型检查包括枚举未选中的变体和空容器的元素类型。无约束泛型参数、函数、JSON、
Flow、Space、Entity、Link、Transform3 不支持直接判等。

数组比较顺序和元素；映射比较键集合及对应值，忽略插入顺序；记录比较同一名义类型的字段；
枚举比较同一名义类型的变体和负载。源码位置、分配地址和对象共享形状不参与值比较。
普通值不提供可变引用环；空间图通过显式实体和连接身份表达环。
比较不修改对象或分配 VM 堆值，运行时遍历受独立工作预算限制，并可取消。
float64 使用精确值比较；几何近似判断应显式选择容差，不把容差隐含在 `==` 中。

状态初值中的常量比较也须保持相同的内容语义，包括不同字段书写顺序与映射插入顺序。
运行时语义测试见 `tests/test_value_equality.py`，编译及 PNG 往返测试见
`tests/test_native_equality.py`。
值更新与隔离验证见 `tests/test_native_value_assignment.py`、`tests/test_native_value_captures.py`
和 `tests/test_native_collection_methods.py`。

## Unicode 分类

`unicodeCategory(text)` 接受单个字符，返回 `Lu`、`Ll`、`Lo` 等两字母 Unicode 通用类别。参考运行时和原生后端均使用随附的 Unicode **15.1.0** 数据表，不随宿主 Python 的 Unicode 版本变化。空字符串或多个字符会被拒绝。

## 继续阅读

- [完整文档目录](../../README.md)
- [语言总览](overview.md)
