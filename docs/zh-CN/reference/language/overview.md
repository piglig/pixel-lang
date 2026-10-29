# 语言总览

[简体中文](overview.md) · [English](../../../en/reference/language/overview.md) · [文档首页](../../README.md)

本页概览声明、类型、表达式和模块。先通过入门教程运行程序，再按链接查询各主题的精确规则。

## 声明与类型

`fn name(arg: T) -> R { ... }` 定义返回值函数；unit 函数省略返回类型。
`fn square(n: int) -> int = n * n` 是表达式函数。模块不允许顶层执行语句；声明包括 fn、record、enum、import。
原生空间模型还提供 `frame` 和 `flow`，已在开发预览中实现；使用方法与产物要求见[原生三维空间](../../guides/spatial-programming.md)。
选定入口模块必须定义无参数、返回 unit 的 main；需要外部效果时写作 `effect fn main()`；导入模块的 main 不会自动执行。
导入使用相对路径或 `std/...`，可通过 `as alias` 指定别名。跨模块使用声明需要显式 export，包括函数、记录、枚举、坐标系和 flow。

类型包括检查溢出的 int64、有限 float64、bool、Unicode 标量序列 string、`[T]`、`map[K, V]`、
名义记录和枚举、`Option[T]`、不可变 json、内建 Error，以及[函数与闭包](functions.md)。
没有隐式转换或空引用；JSON null 是合法的 json 值。
记录类型按声明身份区分：字段相同的两个声明仍是不同类型。同一记录类型的值，
在所有字段均可比较时按字段内容判断相等，见[数据语义](data.md)。

泛型记录显式声明类型参数，如 `record Box[T] { value: T }`；构造可推导为 `Box{value: 42}`，
也可显式写作 `Box::[int]{value: 42}`。
泛型函数可写 `fn identity[T](value: T) -> T`；调用使用推导的 `identity(42)` 或显式的 `identity::[int](42)`。
参数与预期返回类型必须提供一致静态证据，否则报错。匿名函数可以写 `fn(x) = expression` 或块体，
参数类型可来自上下文，返回类型可以推导；具名函数签名仍须显式标注。详见[泛型](generics.md)。

`let x = expression` 推导绑定类型，`let x: T = expression` 显式指定。重新赋值使用 var。
普通函数参数、for 绑定和模式绑定只读。记录和容器采用值语义；
`var` 允许修改本地字段和元素，其他副本保持不变。写回调用者变量须显式使用 `inout`。
空容器需要上下文或显式类型，例如 `let values: [int] = []`、`map[string, int]{}`、`([]: [int])`。

记录声明为 `record Sale { category: string, amount: int }`，构造为 `Sale{category: "books", amount: 42}`。
`Sale{category, amount}` 读取同名局部绑定。每个字段必须恰好提供一次且类型匹配。
字段用点访问，数组和映射用下标访问。`sale with { amount: 50 }` 创建保留其他字段的新记录。
赋值和传参不会产生共享修改，见[数据语义](data.md)。

## 表达式与语句

优先级从高到低：成员/下标/调用；一元 - 和 !；* / %；+ -；< <= > >=；== !=；&&；||。
二元运算左结合，&& 和 || 短路。字符串 + 连接文本。比较操作要求相同的 int、float64 或 string 类型；
比较和相等不进行类型转换。数值操作要求两个操作数同为 int 或 float64，详见[数值](numbers.md)。
相同类型的标量、Point3／Vec3、EntityId／LinkId，以及内容类型均可比较的数组、映射、记录和枚举支持值相等；
函数和 json 不支持直接判等。循环和身份规则见[数据语义](data.md)。整数除法向零截断。

控制流包括 `if condition { ... } else { ... }`、`while condition { ... }`、`for value in collection { ... }`。
双绑定遍历数组得到索引和值，遍历字符串得到从零开始的 Unicode 标量索引和值，遍历映射得到键和值；
单绑定映射遍历得到键。遍历保留进入循环时的值和映射插入顺序；修改其他本地副本不会改变遍历值。

break/continue 作用于最近循环；for 的 continue 会推进索引。
空块合法。在控制头中直接使用记录构造式时，必须加括号以区分构造花括号与语句体。

可恢复失败使用 `Result[T, Error]`，缺失使用 `Option[T]`。用 `match` 处理结果，
在返回兼容类型的函数内用 `?` 显式传播。内建 Result 必须使用。Error 包含
kind、code、message、path、operation、expected、actual 字符串字段，见[错误](errors.md)。
断言失败、算术溢出、资源预算和取消会终止执行，不存在 try/catch/fail 通路。

unit 函数的 return 不带值；返回值函数的每条正常完成路径必须返回值。
同一块内 return、break 或 continue 后的语句报不可达错误。循环保守地视为可能终止，
break 不满足函数的返回要求。检查器不推断任意用户函数必定终止执行。
没有模块变量或嵌套具名函数；匿名函数按值捕获词法绑定，见[函数](functions.md)。

赋值支持 `x = value`、`items[i] = value`、`item.field = value`。可写位置支持对应类型的复合赋值。
接收者和索引从左到右只求值一次；复合赋值先读取旧值，再求右操作数，最后写回。
读取失败时不执行右侧，此前副作用不回滚。整数更新使用 `+= 1`／`-= 1`，不提供 `++/--`。`print(value)` 输出值，独立调用语句可丢弃非 Result 返回值；业务结果规则见[错误](errors.md)。

`jsonDecode::[T](text-or-json)` 遵循[类型化 JSON](json.md)。其他内建操作提供集合、文本、input()、断言、
JSON 检查和授权文件访问。自举前端签名位于 `selfhost/checker_builtins.pxl`，参考签名位于
`pixellang/typesys.py`，库 API 以 `pixellang/stdlib` 的 export 声明为准。

## 语义像素

标识符解析为数字 ID。区域编码声明与语句，包括 var、入口、break、continue、双 for 绑定和 JSON 解码类型。
空分支可表示。元数据名称不决定执行或入口。
见[空间语义](spatial.md)和[像素编码](../formats/encoding.md)。

## 枚举与缺失值

带标签枚举、Option、构造器和穷尽 match 见[枚举](enums.md)。模式绑定不可重新赋值，match 主体只求值一次。

## 继续阅读

- [完整文档目录](../../README.md)
