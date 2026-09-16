# 原生 VM 原型

[中文](native-vm.md) · [English](../../en/internals/native-vm.md) · [文档首页](../README.md)

这是显式启用的实验，不是默认运行时。它用 Cython 将现有 VM 编译为本地扩展，仍使用 CPython 对象、GIL、
受检查语言操作和 Python 依赖；不是独立 C/Rust VM，也不是 PixelLang 机器码后端。
原型隔离解释分派成本，保留同一语义实现，不推导 C 整数或绕过算术检查。VM 源码变化后必须重建再对照。

## 构建与对照

仓库根目录需要本地 C 编译器：

```sh
uv run --no-project --python .venv/bin/python --with Cython==3.1.8 --with setuptools==80.9.0 \
  python scripts/build_native_vm.py --output /tmp/pixel-native-NEW
.venv/bin/python scripts/native_vm_probe.py --output /tmp/pixel-python-NEW
.venv/bin/python scripts/native_vm_probe.py --native /tmp/pixel-native-NEW/manifest.json \
  --output /tmp/pixel-native-measurement-NEW
```

构建环境隔离，不向用户运行依赖加入 Cython。生成 C、注解、二进制和身份存于仓库外。
加载器验证源码哈希、扩展哈希和 ABI，只在探针进程替换 VM，生产服务仍用 Python VM。
同一电源条件下顺序测量相同编译器、源码、预算，先比较 resultSha256 和指令数，再比较时间。
默认 40 函数工作负载编译、导出、恢复并检查两边都输出 42。
--self 使用完整编译器源，恢复后再编译运行新程序；--profile 的计时含探针开销，不能当正式提速数字。

## 语义验证

```sh
.venv/bin/python scripts/native_vm_probe.py --native /tmp/pixel-native-NEW/manifest.json \
  --output /tmp/pixel-native-tests-NEW --test tests.test_compiler_service \
  --test tests.test_runtime --test tests.test_temporal --test tests.test_debugvalues \
  --test tests.test_selfhost_debug
```

覆盖服务/取消、堆/GC 别名、检查点、回放、变量与 Studio 映射，不替代完整发布和跨平台验收。
Python VM 为参考。默认启用前还需平台 wheel、安装和全部运行时回归。
构建遵循 [Cython 编译文档](https://cython.readthedocs.io/en/stable/src/userguide/source_files_and_compilation.html)。

仅比较完整编译器编译，不包含 PNG：

```sh
.venv/bin/python scripts/native_vm_probe.py --self --compile-only --output /tmp/pixel-self-python-NEW
.venv/bin/python scripts/native_vm_probe.py --self --compile-only \
  --native /tmp/pixel-native-NEW/manifest.json --output /tmp/pixel-self-native-NEW
```

输出完整产物供逐字节比较。测试子进程不继承原型加载器，原生执行结论只适用于当前进程 VM。
