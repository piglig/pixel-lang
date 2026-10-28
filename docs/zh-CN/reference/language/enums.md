# 枚举、Option 与模式匹配

[简体中文](enums.md) · [English](../../../en/reference/language/enums.md) · [文档首页](../../README.md)

枚举表达有限的类型化选择，Option 表达缺失。match 检查覆盖范围，并可通过值块产生结果。


**本页内容**

- [规则与接口](#规则与接口)
- [缺失值](#缺失值)
- [上下文变体](#上下文变体)
- [组合模式与守卫](#组合模式与守卫)
- [值块与控制流](#值块与控制流)
- [JSON](#json)
- [执行与验证](#执行与验证)
- [继续阅读](#继续阅读)

## 规则与接口

枚举是具有固定变体和类型化载荷的名义类型：

```go
enum Event[T] { Stop, Data(value: T) }
let event = Event::[int].Data(42)
let answer = match event {
    Event.Stop => 0,
    Event.Data(value) => value
}
```

构造器由枚举类型限定，泛型参数可显式提供，也可由预期枚举类型确定。
类型证据不足时需要类型注解或显式参数。无载荷变体统一写 `Event::[int].Stop`，不带括号。
变体名和载荷字段名在各自作用域内唯一；导出枚举可通过模块别名构造和匹配。
主体能提供参数时，模式可省略泛型参数；显式参数必须与主体完全匹配。

match 主体只求值一次，只有选中分支执行。表达式分支返回相同类型，预期类型分别传给每个分支；
match 表达式也可作为语句使用。模式绑定不可重绑且限于当前分支。负载保持声明中的普通类型 T，不引入 Frozen 投影。需要修改时，将负载赋给本地 var；修改副本不影响原枚举。
载荷通过模式访问，不支持字段选择。
必须覆盖所有可能输入；`_` 匹配任意值。完全被先前分支覆盖的模式、错误载荷数量、未知变体和缺失分支均报错。
所有分支都返回时语句 match 才保证返回；循环控制遵循词法循环作用域，Result 传播遵循所属函数的返回契约。

## 缺失值

`Option[T]` 内建 Some(value: T) 和 None。`Option.Some(42)` 推导为 Option[int]；
`.None` 需要预期类型，如 `let value: Option[int] = .None`。
Option 与普通枚举使用同一套限定名和上下文变体规则；裸 `Some`、`None` 是普通用户名称。
显式 `Option::[int].None`、`Option::[int].Some(42)` 不需要上下文。
普通 T 不隐含缺失，也不隐式转换为 Option[T]。
`values.get(key)` 对 `map[K, T]` 返回 `Option[T]`，存在时为 Some，否则为 None。
取出的值遵循相同的值隔离规则，不需要冻结或解冻。
普通映射下标缺失会导致运行失败；需要处理缺失时使用返回 Option 的 get。

## 上下文变体

已知预期枚举类型时，可以省略类型限定名：

```pixellang
enum Status { Waiting, Paid(amount: int) }
let status: Status = .Paid(100)
let amount = match status {
    .Waiting => 0,
    .Paid(value) => value
}
```

预期类型可来自变量注解、函数参数、返回类型或记录字段；模式从被匹配值取得类型。
泛型调用还可从其他实参取得类型证据。没有预期类型的 `let status = .Waiting`
会报错；编译器不会搜索全局同名变体。需要时写出 `Status.Waiting`。
PNG 保存已解析的名义类型；恢复源码可能使用完整限定名，执行语义保持一致。

## 组合模式与守卫

组合模式同时匹配多个输入，守卫为分支增加条件。

```pixellang
enum Status { Waiting, Paid(amount: Option[int]) }
enum Event { Pay, Cancel }
fn choose(status: Status, event: Event) -> int = match (status, event) {
    (.Waiting, .Pay) => 1,
    (.Paid(.Some(amount)), .Pay) if amount > 0 => amount,
    (.Paid(.Some(amount)), .Pay) => 0,
    (_, _) => -1
}
```

`match (a, b)` 同时匹配多个输入；每个分支的模式数量必须与输入数量一致。
这里的括号是 match 的输入列表，不引入通用元组值。所有输入按从左到右的顺序只求值一次。
枚举负载可以递归匹配；普通名称绑定该位置的整个值，`_` 忽略该位置。
绑定只读，在本分支的守卫和结果中可见。

分支按源码顺序选择。模式全部成功后才计算 `if` 守卫；守卫必须为 bool，
不成立则继续检查下一分支。守卫不计入穷尽覆盖，即使写作 `if true` 也需要无守卫覆盖。
相同外层变体可出现在多个分支；只有整个模式组合已被无守卫分支覆盖时才判为不可达。
PNG 保存每层模式的名义类型、绑定身份与守卫表达式，恢复无需依赖隐藏源码。

## 值块与控制流

match 分支可以使用值块计算结果。

```pixellang
fn choose(value: Option[int]) -> int = match value {
    .Some(n) => { let adjusted = n + 1; adjusted },
    .None => 0
}
```

末尾不带分号的表达式提供块的值；末尾分号丢弃该值，空块的类型为 `unit`。
局部绑定只在块内可见。能正常结束的分支必须具有一致的结果类型。

值块用于计算结果，不允许 `return` 跳出所属函数，也不继承外层循环的
`break`／`continue` 目标。需要提前返回时，在外层语句中使用 `if` 或语句 match。
值块内自己声明的循环可以使用 break／continue；嵌套函数拥有独立的返回与循环上下文。

`?` 仍可按所属函数的 Option／Result 契约传播失败，值块不会创建隐藏函数。
传播失败会跳过后续操作数并释放被放弃的临时访问，之前已经执行的效果和修改不会回滚。

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
`test_enums.py` 覆盖构造、类型错误、泛型模块、JSON、分支副作用、循环、PNG 和回放。
`test_debugvalues.py` 覆盖变体摘要、分页载荷和模式名；`test_enum_editor.py` 覆盖导航、重命名、碰撞拒绝、
泛型补全和不完整注解。真实 VS Code 集成测试覆盖变体补全、定义与重命名。

## 继续阅读

- [完整文档目录](../../README.md)
- [语言总览](overview.md)
