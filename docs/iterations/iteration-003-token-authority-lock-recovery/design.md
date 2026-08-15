# 迭代 003：TokenAuthority 并发锁与恢复设计文档

## 1. 共享锁原语

新增 `backtrader_agent.locking`，导出一个 context manager：

```text
exclusive_file_lock(path, error_code, subject)
```

它创建父目录并以 `O_CREAT|O_RDWR, 0600` 打开稳定文件；POSIX 使用
`fcntl.flock(LOCK_EX|LOCK_NB)`，Windows 使用 `msvcrt.locking(LK_NBLCK, 1)`。
竞争者以短 sleep 重试直到有限超时。退出时 unlock、close，但不删除路径。所有 OS
错误由调用者传入的 code 映射，避免将路径或原始异常泄露给 CLI。

`SessionStore._locked()` 改为薄包装该原语，仍把失败映射为
`BTAG-SESSION-LOCK`；现有 session lock file 位置不变。

## 2. TokenAuthority 锁边界

```text
<state-root>/token-secret.lock
<state-root>/approvals/<request-id>.lock
```

`_secret()` 在 secret lock 内执行 exists/read/create/chmod/read。这样首次调用者只
有一个会写入；排队调用者直接读取已写入字节。若与旧进程发生 create-only 冲突，
只在确认路径现已存在时转为读取，其他持久化错误继续失败。

`_approval_lock()` 保留原有 request-id 校验和路径，以便历史遗留 `.lock` 文件自然
成为稳定 OS lock file。它不再用 `O_EXCL` 表达锁，也不再 unlink。`grant_approval()`
与 `consume()` 的现有 read/validate/replace 序列因此在同一 OS 临界区内运行。

## 3. 锁顺序与恢复

签名计算可能在 approval 临界区内短暂取得 secret lock；`_secret()` 释放该锁后才返回。
反向路径不会在持有 secret lock 时取得 approval/session lock。grant 的顺序为
approval →（短暂 secret）→ session；run/change 的顺序为 approval consume 完成后再
取得 session lock，因此不存在循环等待。

异常退出时 OS 会释放 descriptor 锁，但保留文件。下一次调用重新打开同一路径，既可
继续操作，也不会把残留路径误判为“永远 busy”。

## 4. 测试设计

- secret red/green 测试使用 spawn worker 和屏障，在首次写入窗口产生真实
  `create_only` 竞争；绿色要求全部 worker 成功且 secret 一致。
- approval 测试分别覆盖遗留路径、两个真实进程的竞争等待、不同 request ID 的独立
  路径和 lock OS error 映射。
- SessionStore 的既有同 session create/transition/recover 测试继续运行，确保共享
  原语未改变 journal/checkpoint 契约。
