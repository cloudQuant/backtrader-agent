# 迭代 009：Adapter 生命周期串行化设计文档

## 1. Lock namespace

对已解析 external target 使用每-host 的 stable file：

```text
<target>/.backtrader-agent/installer/<host>.lock
```

Installer 复用 `exclusive_file_lock()`，以 `BTAG-INSTALL-LOCK` 为所有底层错误 code。lock 文件不属于
install manifest，uninstall 不删除它，避免并发 opener 锁定被替换 inode。

## 2. Apply 与 preview 的边界

`apply=False` 继续只计算/校验 preview，不进入 lock、也不调用 mkdir。`apply=True` 在获得 lock 后重新
执行 preflight，再执行 create-or-verify 或 uninstall hash 校验与 unlink。这样第一次操作的完整提交成为
第二次操作的唯一观察状态：

```text
apply caller A: acquire -> recheck -> mutate -> result -> release
apply caller B: wait    -> recheck committed state -> domain result -> release
```

双 uninstall 的 B 不会保存过时 removal list；它在 lock 内发现 manifest 缺失并返回
`BTAG-UNINSTALL-MANIFEST`。不同 host 派生不同 lock path，仍可并行。

## 3. 测试设计

在两个 spawn uninstall worker 内包装实际 `Path.unlink`，让旧实现同时抵达第一个 adapter deletion；旧
行为一个 success、一个 raw `FileNotFoundError`。正确锁会使 barrier 超时而非让第二 worker进入 unlink，
随后得到一个 `uninstalled` 与一个 `BTAG-UNINSTALL-MANIFEST`。附加 lock isolation、fault mapping 和
preview zero-write 断言。
