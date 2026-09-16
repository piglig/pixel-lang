# PixelLang 语言参考（0.8）

[中文](overview.md) · [English](../../../en/reference/language/overview.md) · [文档首页](../../README.md)

本页定义当前语言行为。PixelLang 使用自有语法；UTF-8 文本和语义像素都是编程表示。
两者编译为 PixelVM 字节码，文本编译不需要经过 PNG 往返。

## 声明与类型

`fn name(arg: T) -> R { ... }` 定义返回值函数；unit 函数省略返回类型。
`fn square(n: int) -> int = n * n` 是表达式函数。模块只允许 fn、record、enum、import 声明。
选定入口模块必须定义无参数、返回 unit 的 `fn main()`；导入模块的 main 不会自动执行。
导入使用相对路径或 `std/...`，可通过 `as alias` 指定别名。只有显式 export 的函数、记录和枚举跨模块可见。

类型包括检查溢出的 int64、有限 float64、bool、Unicode 标量序列 string、`[T]`、字符串键的 `map[T]`、
名义记录和枚举、`Option[T]`、不可变 json、内建 Error，以及[函数与闭包](functions.md)。
内部类型使用 str、T[]、T{} 和名义类型 ID。没有隐式转换或空引用；JSON null 是合法的 json 值。
记录按声明身份区分，不采用结构相等。

泛型记录显式声明和实例化，如 `record Box[T] { value: T }`、`Box[int]{value: 42}`。
泛型函数可写 `fn identity[T](value: T) -> T`；调用使用推导的 `identity(42)` 或显式的 `identity[int](42)`。
参数与预期返回类型必须提供一致静态证据，否则报错。匿名函数可以写 `fn(x) = expression` 或块体，
参数类型可来自上下文，返回类型可以推导；具名函数签名仍须显式标注。详见[泛型](generics.md)。

`let x = expression` 推导绑定类型，`let x: T = expression` 显式指定。重新赋值使用 var。
函数参数、for 绑定和 catch 绑定不可重新绑定，但数组、映射和记录的内容仍可变；Error 字段只读。
空容器需要上下文或显式类型，例如 `let values: [int] = []`、`map[int]{}`、`([]: [int])`。

记录声明为 `record Sale { category: string, amount: int }`，构造为 `Sale{category: "books", amount: 42}`。
`Sale{category, amount}` 读取同名局部绑定。每个字段必须恰好提供一次且类型匹配。
字段用点访问，数组和映射用下标访问；别名共享可变对象。

## 表达式与语句

优先级从高到低：成员/下标/调用；一元 - 和 !；* / %；+ -；< <= > >=；== !=；&&；||。
二元运算左结合，&& 和 || 短路。字符串 + 连接文本。比较操作要求相同的 int、float64 或 string 类型；
比较和相等不进行类型转换。数值操作要求两个操作数同为 int 或 float64，详见[数值](numbers.md)。
容器、记录、枚举、函数和 json 不支持相等比较。整数除法向零截断。

控制流包括 `if condition { ... } else { ... }`、`while condition { ... }`、`for value in collection { ... }`。
双绑定遍历数组得到索引和值，遍历字符串得到从零开始的 Unicode 标量索引和值，遍历映射得到键和值；
单绑定映射遍历得到键。进入循环时只创建一次外层浅快照，保留映射插入顺序和入口值；嵌套对象仍共享引用。

break/continue 作用于最近循环并正确退出内部错误处理器；for 的 continue 会推进索引。
空块合法。在控制头中直接使用记录构造式时，必须加括号以区分构造花括号与语句体。

`try { ... } catch error { ... }` 捕获数据错误。Error 包含只读字符串字段
kind、code、message、path、operation、expected、actual，见[错误](errors.md)。未捕获错误终止执行。
断言失败、算术溢出、资源预算和取消不可捕获。unit 函数 return 不带值，返回值函数的每条路径必须返回。
循环保守地视为可能终止；直接不可达语句报错。没有模块变量或嵌套具名函数；匿名函数可以捕获词法绑定。

赋值支持 `x = value`、`items[i] = value`、`item.field = value`。可写位置支持对应类型的复合赋值。
接收者和索引从左到右只求值一次；复合赋值先读取旧值，再求右操作数，最后写回。
读取失败时不执行右侧，此前副作用不回滚。++/-- 仅用于 int。`print(value)` 输出值，独立调用语句丢弃返回值。

`jsonDecode[T](text-or-json)` 遵循[类型化 JSON](json.md)。其他内建操作提供集合、文本、input()、断言、
JSON 检查和授权文件访问。自举前端签名位于 `selfhost/checker_builtins.pxl`，参考签名位于
`pixellang/typesys.py`，库 API 以 `pixellang/stdlib` 的 export 声明为准。

## 语义像素

标识符解析为数字 ID。区域编码声明与语句，包括 var、入口、break、continue、双 for 绑定和 JSON 解码类型。
空分支可表示，catch 的存在与其体长度独立。元数据名称不决定执行或入口。
见[空间语义](spatial.md)和[像素编码](../formats/encoding.md)。

## 枚举与缺失值

带标签枚举、Option、构造器和穷尽 match 见[枚举](enums.md)。模式绑定不可重新赋值，match 主体只求值一次。
