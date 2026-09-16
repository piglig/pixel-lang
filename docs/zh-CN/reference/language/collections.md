# 集合与泛型回调（语言 0.8）

[中文](collections.md) · [English](../../../en/reference/language/collections.md) · [文档首页](../../README.md)

| 内建操作 | 行为 |
| --- | --- |
| `extend(destination, source)` | 顺序追加 source，修改并返回 destination；元素类型相同。自身追加只复制原内容一次。 |
| `arrayRepeat(values, count)` | 返回将输入序列重复 count 次的新外层数组；count 是非负 int，零产生空数组。 |

两者均为浅操作，元素引用共享。分配/扩展前检查堆项预算，GC 时保留参数引用。
文本和像素程序都可调用，它们不解释编译器或图片数据。单条 VM 指令仍可能处理大量元素，
因此性能需同时记录墙钟耗时与指令数。

导入 `std/collections.pxl`。库由 PixelLang 编写，以语义像素交付，使用普通泛型函数值与闭包。
类型参数可从实参和回调签名推导；例如 `Map(values, fn(value) = string(value))`，也可显式标注。

| 函数 | 回调 | 结果 |
| --- | --- | --- |
| `Map[T, U](values, transform)` | `fn(T) -> U` | 新 `[U]` |
| `Filter[T](values, predicate)` | `fn(T) -> bool` | 新 `[T]` |
| `Fold[T, A](values, initial, combine)` | `fn(A, T) -> A` | 累积 A |
| `GroupBy[T](values, keyOf)` | `fn(T) -> string` | `map[[T]]` |
| `Sort[T](values, less)` | `fn(T, T) -> bool` | 新排序 `[T]` |

Map、Filter、Fold、GroupBy 在入口建立浅遍历快照，按输入顺序访问一次。
回调对外层数组的修改不改变访问序列，元素对象仍共享。GroupBy 每元素调用 keyOf 一次，
组内保留顺序，组按首次出现顺序插入。空输入不调用回调，Fold 原样返回 initial。

Sort 使用稳定的自底向上归并排序，比较次数 O(n log n)，辅助空间 O(n)。
调用比较器前复制外层数组，不重排输入数组；同序元素保留原顺序，元素对象不深复制。
比较器应定义一致严格顺序。单次执行的调用顺序确定，但不应依赖跨算法版本的精确比较序列。

回调可捕获绑定或产生可捕获错误，错误立即停止算法并向调用者处理器传播。
已有副作用不回滚；Sort 的副本也不会撤销回调对原数组或共享对象的主动修改。
`tests/test_collection_callbacks_v6.py` 使用独立排序预期，覆盖空、重复、有序、逆序、随机输入、
稳定记录、别名、副作用、错误、PNG/文本恢复和中间回放状态。
`examples/callback-analysis` 组合可选 JSON 配置、捕获筛选、稳定记录排序、映射、浮点聚合和分组。
运行 `pixel test examples/callback-analysis`。
