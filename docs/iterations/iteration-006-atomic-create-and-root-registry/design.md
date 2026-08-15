# 迭代 006：原子 create-only 与 RootRegistry 线性化设计文档

## 1. create-only no-clobber publish

继续在 destination 同目录以 `mkstemp` 写入、flush 和 fsync 临时文件。`create_only=False` 保持
`os.replace()`；`create_only=True` 则使用同一文件系统内的 hard-link 创建目标路径：

```text
temporary (fully fsynced)
  -- os.link(temporary, destination) --> destination
  -- unlink temporary
```

hard-link 的 destination 创建是 no-replace 操作：若另一个进程已经发布，`FileExistsError` 映射为
`BTAG-WRITE-EXISTS`，而不是覆盖它。临时文件最终路径仍由现有 finally 清理；成功创建后继续对
parent directory 尝试 fsync。临时文件和 destination 在同目录，保证同一 filesystem。

## 2. RootRegistry 锁边界

新增稳定路径：

```text
<state-root>/root-registry.lock
```

`RootRegistry._locked()` 使用已有 `exclusive_file_lock()`，错误 code 为 `BTAG-ROOT-LOCK`。仅
`register()` 在锁内完成 `_load()`、同 ID 比较、dict update 与 `atomic_write_json()`；读操作继续
读取 atomic-replace 的完整 manifest，不为每次 path resolve 添加长锁。锁文件永不 unlink。

## 3. 测试设计

- create-only red test 在两个 spawn worker 内包装旧 `os.replace`，让它们在两个 exists check
  之后、publish 前汇合；旧实现两个 worker 都 success 且后写者覆盖。新实现不调用 replace
  publish，结果必须是一个 success、一个 `BTAG-WRITE-EXISTS`，目标是完整赢家 bytes。
- RootRegistry red test 在两个 worker 的 write 边界汇合：旧实现两者 success 但最终只含一个 ID；
  新 registry lock 使 barrier 被正确打破，两个 ID 均保留。
- 覆盖同 ID 幂等/冲突、root lock path 保留、open/acquire/release 错误映射和 descriptor close。
