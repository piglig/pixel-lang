# 函数、效果与闭包

[简体中文](functions.md) · [English](../../../en/reference/language/functions.md) · [文档首页](../../README.md)

函数签名描述参数、结果、修改权限和外部效果。闭包捕获创建时的值，调用遵循确定的求值顺序。


**本页内容**

- [规则与接口](#规则与接口)
- [函数类型与效果权限](#函数类型与效果权限)
- [匿名函数与按值捕获](#匿名函数与按值捕获)
- [求值、交付与调试](#求值交付与调试)
- [继续阅读](#继续阅读)

## 规则与接口

开发编译器统一使用 `fn`。普通函数没有外部效果；需要 I/O 或创建、推进运行实例时，
显式使用 `effect fn`。旧 `component` 声明和函数类型不再接受。

```pixellang
fn Twice(value: int) -> int = value * 2
effect fn main() { print(Twice(21)) }
```

## 函数类型与效果权限

函数类型写作 `fn(T, U) -> R`；unit 返回可省略箭头，如 `fn(int)`。
允许外部效果的函数类型写作 `effect fn(T) -> R`。参数名不参与类型身份。
`effect` 仅在紧邻 `fn` 时作为修饰符，其他位置仍可用作普通标识符。

无外部效果的函数可用于允许外部效果的回调位置，反向不允许；`inout` 访问模式仍须匹配。参数类型逆变、返回类型协变；
容器和名义类型的类型参数不因此获得隐式转换。

```pixellang
fn Apply(value: int, operation: fn(int) -> int) -> int = operation(value)
effect fn Report(value: int) { print(value) }
effect fn Visit(value: int, operation: effect fn(int)) { operation(value) }
effect fn main() { Visit(Apply(3, Twice), Report) }
```

检查依据函数声明的契约，即使某个 `effect fn` 当前为空实现，未声明 `effect` 的函数也不能调用它。
反过来，有效果的调用者不会向未声明 `effect` 的被调用者授予 I/O 权限。函数变量、记录中的回调、
泛型和动态调用都保留效果信息；运行时同样检查调用边界。

函数实现违反效果契约时，在该定义处诊断，不把实现中的效果推断传播给调用者。
调用依据表达式的静态函数类型；即使 `let alias: effect fn() = pure` 的目标没有外部效果，
调用 alias 仍需要效果权限。已成功检查且语义依赖未变的函数可复用效果检查结果。

局部 `var` 修改不属于外部效果。显式参数写回使用独立的访问权限：

```pixellang
fn Increment(value: inout int) { value += 1 }
effect fn main() {
    var count = 0
    Increment(&count)
    print(count)
}
```

函数类型保留 `inout`，如 `fn(inout int)`。参数和调用处都必须显式标记，
访问不得逃逸或被闭包捕获。同一根变量的重叠可写访问会被拒绝；不同变量可以分别写入。
`&` 仅用于调用参数，不是通用引用值。

`fn` 保证没有外部效果，不保证没有调用者可观察的修改。带 `inout` 的函数会写回实参，
不能仅凭缺少 `effect` 就缓存调用结果、合并调用或重排执行。局部值修改与参数写回
也不能混为一谈：前者不会改变调用者的值，后者必须保留显式写回。

## 匿名函数与按值捕获

匿名函数使用 `fn(x: T) -> R { ... }` 或 `fn(x: T) = expression`。
上下文提供类型时可省略参数注解，例如 `fn(x) = x + 1`；缺少类型证据时报告错误。
有外部效果的匿名函数显式写作 `effect fn(...)`。

所有闭包捕获创建时的值，捕获内容只读。后续外层变量修改不影响已创建的闭包：

```pixellang
effect fn main() {
    var offset = 2
    let add = fn(value: int) -> int = value + offset
    offset = 10
    print(add(3)) // 5
}
```

嵌套闭包和每次循环迭代遵守同样规则。需要持续变化的实例状态时使用 flow；
闭包不能通过捕获外层变量获得隐蔽的共享修改能力。捕获普通值不需要权限转换；
`inout` 访问不能被捕获。整体实现进度见[设计原则](../../internals/design.md)。

## 求值、交付与调试

先求值可调用表达式，再从左到右求值各参数，每项只执行一次。参数求值过程中替换
原先保存函数的变量，不会改变本次已选定的调用目标。

PNG 保存函数类型、效果、匿名函数体身份和捕获关系。源码恢复后重新检查同一契约，
入口包装函数保留所需效果权限。字节码使用 `CAPTURE_VALUE` 创建只读捕获单元，
旧 `CAPTURE` 指令被拒绝。检查点与回放保持捕获值，不通过重读外层变量重建闭包。

相关验证包括 `test_function_effect_runtime.py`、`test_native_function_effects.py`、
`test_value_captures.py`、`test_native_value_captures.py` 与 `test_native_inout_png.py`。

## 继续阅读

- [完整文档目录](../../README.md)
- [语言总览](overview.md)
