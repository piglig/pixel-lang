# 集合操作与算法

[简体中文](collections.md) · [English](../../../en/reference/language/collections.md) · [文档首页](../../README.md)

数组和映射遵循统一值语义。基本操作使用接收者方法，通用算法使用 std/collections.pxl，不提供同义入口。


**本页内容**

- [规则与接口](#规则与接口)
- [标准库回调](#标准库回调)
- [类型化映射](#类型化映射)
- [继续阅读](#继续阅读)

## 规则与接口

数组和映射遵循[值语义](data.md)。修改 `var` 中的容器不会改变此前保存的值，
嵌套容器遵守同样规则。

基本容器操作使用小写接收者方法；通用算法使用 `std/collections.pxl` 的大写命名函数，
与其他标准库模块一致。类型和导出权限由声明决定，不由名称大小写决定。
不为同一操作提供额外的全局函数或方法别名。

| 操作 | 行为 |
| --- | --- |
| `values.append(value)` | 向可写数组追加一个元素。 |
| `values.extend(source)` | 顺序追加 source；自身追加只复制原内容一次。 |
| `values.pop()` | 移除末尾元素并返回 `Option[T]`；空数组返回 None。 |
| `table.has(key)` | 查询键是否存在，返回 bool。 |
| `table.keys()` | 按插入顺序返回键数组 `[K]`。 |
| `table.get(key)` | 返回 `Option[V]`；键缺失时返回 None。 |
| `table.remove(key)` | 删除并返回 `Option[V]`；键缺失时返回 None。 |
| `table[key] = value` | 在可写映射中插入或替换值。 |

修改接收者须来自可写本地绑定或 `inout` 参数；普通参数与捕获值只读。
`let` 中的映射可以调用 `get`，不能调用 `remove`。同一根变量的重叠可写访问被拒绝。

```pixellang
effect fn main() {
    let original = [1]
    var edited = original
    edited.extend([2, 3])
    print(original) // [1]
    print(edited)   // [1, 2, 3]
    print(edited.pop()) // Some(3)
}
```

集合查询使用接收者方法 `table.get(key)`，不提供同义的全局 lookup 或标准库 Get。
普通赋值已经保证值隔离，不需要 Copy 函数。

## 标准库回调

导入 `std/collections.pxl`。回调统一使用普通 `fn`：没有外部效果，参数只读，
结果保持值隔离。类型参数可从实参与回调签名推导，例如
`collections.Map(values, fn(value) = string(value))`。

| 函数 | 回调 | 结果 |
| --- | --- | --- |
| `Map[T, U](values, transform)` | `fn(T) -> U` | `[U]` |
| `Filter[T](values, predicate)` | `fn(T) -> bool` | `[T]` |
| `Fold[T, A](values, initial, combine)` | `fn(A, T) -> A` | `A` |
| `GroupBy[T, K: Hashable](values, keyOf)` | `fn(T) -> K` | `map[K, [T]]` |
| `SortedBy[T](values, less)` | `fn(T, T) -> bool` | 排序后的 `[T]` |

回调可以修改本地 `var`，不能修改参数或捕获值，也不能打印或执行文件 I/O。
闭包捕获创建时的值。需要持续更新的累积器时，使用 Fold，或显式循环配合 `inout`。
完整规则见[函数与效果](functions.md)。

Map、Filter、Fold、GroupBy 按输入顺序访问一次。GroupBy 每个元素调用 keyOf 一次，
组内保持顺序，组按首次出现顺序插入。空输入不调用回调，Fold 原样返回 initial。
结果保留普通 T／U，不引入 Frozen 类型；赋给 var 后即可修改，输入和其他结果保持不变。

SortedBy 使用稳定的自底向上归并排序，比较次数 O(n log n)，不重排输入。
同序元素保持原顺序；比较器应定义一致严格顺序。调用顺序在单次执行中确定，
不承诺跨算法版本保持完全相同的比较序列。比较次数不包含回调及容器存储成本。

其他纯函数：

| 函数 | 行为 |
| --- | --- |
| `Repeat[T](value, count)` | 返回 `Result[[T], Error]`；非负 count 生成重复值数组，负数返回 `collection.negative_count`。 |
| `Contains[T: Equatable](values, target)` | 按值查找，最坏 O(n) 次相等比较。 |
| `Unique[T: Equatable](values)` | 保持首次出现顺序，最坏 O(n²) 次相等比较。 |
| `Sorted[T: Ordered](values)` | 数值或字符串按自然顺序稳定排序，返回 `[T]`。 |
| `Sum[T: Numeric](values, initial)` | 按输入顺序累加，返回 T；空输入返回 initial。 |

集合调用不建立新的 flow 状态事务。可处理的缺失值使用 Option，可恢复失败使用 Result；
越界等执行故障终止执行，详见[错误处理](errors.md)。

`Sorted(values)` 使用自然顺序；`SortedBy(values, less)` 使用自定义比较器。两者都返回新数组，保留输入，不提供独立的整数排序模块。

## 类型化映射

同时声明键和值的类型：`map[K, V]`，例如 `map[int, string]{7: "paid"}`。
K 必须满足 [Hashable](generics.md)，V 必须是值类型。类型参数保持不变性，
不会隐式转换数值或字符串。空映射需要能确定两种类型的注解或上下文。

键可能缺失时使用 `table.get(key)`。当前 `table[key]` 在缺失时仍产生
`collection.missing_key` 错误。`table.has(key)` 检查是否存在，`table.keys()`
返回 `[K]`。单变量遍历得到键，双变量遍历得到键和值；遍历使用进入循环时的值。
插入顺序稳定：替换值保持位置，删除后重新插入则排在末尾。
构造时拒绝相等的重复键，浮点正零与负零也属于同一个键。

JSON 对字符串键使用对象，其他映射使用有序的 `{"key": ..., "value": ...}` 条目数组。
类型化解码检查键和值并拒绝重复键。空间载荷、flow 状态和回放保存值，
不会持久化堆地址或哈希值。

专项验证见 `test_native_collection_methods.py`、`test_stdlib_contracts.py`、
`test_native_generic_values.py` 与 `test_collection_callbacks.py`。
`examples/callback-analysis` 提供筛选、稳定记录排序、映射、聚合和分组示例。

`GroupBy` 的键类型从回调返回值推导，支持所有 `Hashable` 类型，例如
`collections.GroupBy([1, 2, 3], fn(value: int) = value % 2)` 返回 `map[int, [int]]`。

`collections.Increment(&counts, key)` 修改 `map[K, int]` 直方图：缺失键从 1 开始，
已有键加 1，整数溢出终止执行。`K` 必须满足 `Hashable`；其他副本保持不变。

## 继续阅读

- [完整文档目录](../../README.md)
- [语言总览](overview.md)
