# 迭代 007：受控运行 action 串行化设计文档

## 1. 稳定 action lock

`ControlledRunner` 以现有 action record 的 key digest 派生 lock，而不是使用短生命周期临时锁：

```text
<state-root>/actions/run-<sha256(idempotency-key)>.lock
```

`exclusive_file_lock()` 保留该文件，因而等待者永远锁定同一 inode。Runner 映射该 abstraction 的
所有底层错误为 `BTAG-RUN-ACTION-LOCK`。lock 获取时间为调用者已允许的 child timeout 加固定完成
余量；这避免默认 30 秒在合法长运行时把第二个同 key caller错误地报告为 lock 故障。

## 2. 锁边界与线性化点

所有不可逆/可见 effect 从 action-record existence check 开始到成功 session 收束结束都在 lock 内：

```text
validate immutable inputs outside lock
  -> acquire run action lock
      -> check action record / request conflict
      -> consume same effect token
      -> session begin or resume
      -> child process (at most once)
      -> persist exact manifest/result/reports/action record
      -> session COMPLETED
  -> release lock
```

完成 action record 是同 key caller 的线性化可观察点。进程在它之前崩溃时，OS release lock；重试者
在 lock 内看见 `RUNNING` 或 partial persisted result 并沿既有 resume path 完成。进程在它之后崩溃
时，重试者只验证/重放持久化结果。

## 3. 测试设计

- 通过 spawn-safe probe runner 将第一个 worker 阻塞在实际 `subprocess.run` 调用边界；启动第二个
  相同 key worker。旧实现让第二个 worker也达到 child boundary，说明 duplicate launch。
- 修复后在释放第一个 worker前，第二个 worker不得达到 child boundary；释放后两者成功返回相同
  result，child-start 计数严格为一。
- 验证 different request 同 key 在 lock 内拒绝，different key lock 独立，error mapping/descriptor
  close，以及既有 run crash-resume。
