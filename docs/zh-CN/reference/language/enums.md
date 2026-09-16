# 带标签枚举、Option 与 match（语言 0.8）

[中文](enums.md) · [English](../../../en/reference/language/enums.md) · [文档首页](../../README.md)

枚举是具有固定变体和类型化载荷的名义类型：

```go
enum Event[T] { Stop, Data(value: T) }
let event = Event[int].Data(42)
let answer = match event {
    Event.Stop => 0,
    Event.Data(value) => value
}
```

构造器由枚举类型限定，泛型构造要求显式参数。无载荷变体可写 `Event[int].Stop` 或 `Event[int].Stop()`。
变体名和载荷字段名在各自作用域内唯一；导出枚举可通过模块别名构造和匹配。
主体能提供参数时，模式可省略泛型参数；显式参数必须与主体完全匹配。

match 主体只求值一次，只有选中分支执行。表达式分支返回相同类型，预期类型分别传给每个分支；
语句 match 使用块分支。模式绑定不可重绑且限于当前分支，引用的容器和记录仍共享可变身份。
载荷通过模式访问，不支持字段选择。
必须覆盖全部变体或以 `_` 通配符结尾。重复变体、错误载荷数量、未知变体、不可达通配符和缺失分支均报错。
所有分支都返回时语句 match 才保证返回；分支内仍遵循循环控制和错误处理器展开规则。

## 缺失值

`Option[T]` 内建 Some(value: T) 和 None。`Some(42)` 推导为 Option[int]；
裸 None 需要预期类型，如 `let value: Option[int] = None`。
显式 `Option[int].None`、`Option[int].Some(42)` 不需要上下文。
普通 T 不隐含缺失，也不隐式转换为 Option[T]。
`lookup(values, key)` 对 map[T] 返回 Option[T]：存在时 Some 保留对象身份，否则 None。
普通映射下标缺失仍抛出可捕获错误。

## JSON

JSON null 解码为 None，其他值按 T 解码为 Some；记录中缺失的 Option 字段也变为 None。
普通必填字段仍必需，未知字段报错。None 编码为 null，Some 编码为载荷；不区分缺失字段与显式 null。
相邻的 Option[Option[T]] 无法区分 None 和 Some(None)，因此不能解码，编码时报 json.unsupported_type。
中间插入容器可支持，例如 Option[[Option[int]]] 能编码 `[null, 3]`。

自定义枚举使用显式对象：

```json
{"variant": "Data", "fields": {"value": 42}}
```

两个顶层键必须存在且不能有额外键，载荷字段严格匹配声明。
未知标签报 json.unknown_variant，载荷错误保留嵌套字段路径。

## 执行与验证

声明、实参、构造器和模式编码在像素中，VM 使用具体模式和检查后的枚举操作。
无需隐藏源码；枚举对象作为载荷引用的 GC 根并参与确定性回放。
`test_enums_v6.py` 覆盖构造、类型错误、泛型模块、JSON、分支副作用、循环、PNG 和回放。
`test_debugvalues.py` 覆盖变体摘要、分页载荷和模式名；`test_enum_editor_v6.py` 覆盖导航、重命名、碰撞拒绝、
泛型补全和不完整注解。真实 VS Code 集成测试覆盖变体补全、定义与重命名。
