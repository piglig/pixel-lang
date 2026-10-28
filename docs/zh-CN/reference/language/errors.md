# Result 与错误传播

[简体中文](errors.md) · [English](../../../en/reference/language/errors.md) · [文档首页](../../README.md)

可恢复失败通过 Result 返回。调用方显式处理或传播，执行故障和取消则终止当前执行。


**本页内容**

- [规则与接口](#规则与接口)
- [类型化业务结果](#类型化业务结果)
- [必须使用的结果](#必须使用的结果)
- [显式传播](#显式传播)
- [受检查的数据操作](#受检查的数据操作)
- [继续阅读](#继续阅读)

## 规则与接口

可恢复失败使用 `Result[T, E]`，缺失值使用 [Option](enums.md)，`?` 显式传播失败。
语言不提供 `try/catch` 或内建 `fail`；可恢复失败与执行终止遵循下文的不同规则。

数据与 I/O 操作返回的 `Result[T, Error]` 在 Err 中保存结构化 Error；通过 `match` 读取其字符串字段：

| 字段 | 含义 |
| --- | --- |
| kind | json、io、numeric、collection、text、value、user 等类别 |
| code | 可用于程序分支的稳定点分错误代码 |
| message | 人类可读消息，措辞不作为分支契约 |
| path | JSON 路径、请求的相对文件路径、集合键/索引，或空字符串 |
| operation | 失败操作，如 jsonDecode、read、write、parseInt |
| expected | 适用时给出预期类型/值，否则为空 |
| actual | 适用时给出实际类型/值，否则为空 |

JSON 代码包括 json.invalid_syntax、json.duplicate_key、json.type_mismatch、json.missing_field、
json.unknown_field、json.numeric_range、json.missing_key、json.index_bounds、json.structure_limit、json.cycle。
路径以 $ 开始，标识符字段用 .name，其他键用 ["quoted.key"]，数组用 [i]。
缺失字段路径指向该字段，jsonDecode 的解析失败使用 $；上下文在嵌套转换中保留且只附加一次。

文件代码包括 io.access_denied、io.not_found、io.invalid_path、io.invalid_file、io.too_large、
io.invalid_encoding、io.no_space、io.unsupported_host、io.failure。
分类基于显式能力/路径检查和操作系统错误码，不匹配消息文本。
文件观测记录结构化错误，回放时不再访问文件系统，即使外部状态已改变。

整数和浮点解析使用 `numeric.invalid_text`、`numeric.range` 等代码。
直接索引越界、算术除零和非法容器操作属于执行故障；需要查询缺失值时使用返回 Option 的接口。
业务错误可以直接使用领域枚举作为 E，无须构造或抛出异常。

message、expected、actual 最多 256 字符，path 最多 1024，operation 最多 64。
文件记录预算计入结构化错误载荷。断言、算术溢出、VM 限制和取消不可捕获。
诊断 phase 与 Error.kind 是不同维度。

## 类型化业务结果

新实现使用内建类型 `Result[T, E]` 表达可预期的业务失败，不需要导入类型。
随附开发编译器已支持该类型；`std/result.pxl` 只提供组合函数。
`Ok(value: T)` 保存成功值，`Err(error: E)` 保存领域错误；E 可为带类型化负载的枚举。
例如支付金额错误可以携带期望金额与实际金额，而不依赖字符串消息解析。

```pixellang
enum PaymentError { AmountMismatch(expected: int, actual: int), AlreadyPaid }
fn Pay(amount: int) -> Result[int, PaymentError] {
    if amount != 100 {
        return .Err(PaymentError.AmountMismatch(100, amount))
    }
    return .Ok(amount)
}
```

调用者通过 `match` 处理 Ok 与 Err，再对错误枚举进行穷尽匹配。
遗漏 match 分支、传入错误负载类型都会被拒绝。`Result` 内建必须使用规则，禁止将结果直接作为表达式语句丢弃。
Result 使用原生类型检查、相等比较、JSON、PNG 与调试机制。

导入 `std/result.pxl` 后，可使用以下组合函数：

- `result.Map(value, transform)` 只变换成功值，保留错误类型。
- `result.MapError(value, transform)` 只变换错误值，保留成功类型。
- `result.AndThen(value, next)` 仅在成功时执行下一步；next 返回同一错误类型的 Result。

这些函数均为普通纯函数，回调使用 `fn`；未选中的回调不执行。结果遵循值语义，后续本地修改不影响输入。
它们不捕获运行时异常，也不把错误消息自动转换为领域错误。

Err 是正常返回的值，不会自动回滚 flow。
状态机应显式选择保留旧状态还是生成下一状态，并把业务拒绝作为可观察输出。
取消、资源限制与不可捕获故障仍遵循运行时规则；预期拒绝与执行故障应分别处理。

## 必须使用的结果

内建 `Result[T, E]` 不可直接作为表达式语句丢弃。普通记录和枚举不携带额外的必须使用标记。
调用结果可以绑定、返回、传给其他函数或通过 match 检查。确实不关心业务结果时，
写 `result.Ignore(Pay(...))`，明确承担忽略失败的决定；该函数仍会先执行 Pay。
这项规则检查表达式语句，不是线性类型或全程序未使用变量分析：绑定后未读取仍合法，
容器不继承元素的不可丢弃规则。执行故障不会被 Ignore 捕获。
PNG 保存 Result 类型；恢复源码后重新执行相同检查，不依赖名称元数据。

## 显式传播

`value?` 只求值一次：Ok／Some 解包成功值，Err／None 从当前函数返回。
成功类型可以改变，Result 的错误类型必须相同；需要转换错误时先调用 MapError。
`?` 不会跨过闭包边界，不会撤销已执行的 inout 修改。

```pixellang
fn Receipt(amount: int) -> Result[string, PaymentError] {
    let paid = Pay(amount)?
    return .Ok("paid " + string(paid))
}
```

函数需要声明 Result 或 Option 返回类型，局部闭包也可以从上下文得到该类型。
flow 中通过普通函数处理结果，再显式决定 next 和 output；业务失败不自动回滚。

## 受检查的数据操作

随附开发编译器中，数字解析、UTF-8 解码、浮点字节解码、压缩／解压、JSON 解析／访问／
类型检查及文件操作返回 `Result[T, Error]`。`jsonDecode::[T](text)` 返回
`Result[T, Error]`，T 本身可以是 Result。调用者通过 match 处理，或在返回 Result
的函数中使用 `?` 传播。

```pixellang
fn AddOne(text: string) -> Result[int, Error] = .Ok(parseInt(text)? + 1)
```

只有预期的数据／I/O 失败会变成 Err。取消、资源耗尽、无效字节码和效果违规仍是
执行故障；该边界不会自动撤销此前的写入。字节编解码器将非法数据作为 Error 值返回；
例如 `zlibCompress([256])` 返回 Err，code 为 `bytes.range`，operation 为 `zlibCompress`。
调用者应通过 match 或 `?` 处理结果；编解码失败不会使输入数组失效或消耗其内容。

## 继续阅读

- [完整文档目录](../../README.md)
- [语言总览](overview.md)
