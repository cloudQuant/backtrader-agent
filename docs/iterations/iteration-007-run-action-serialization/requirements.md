# 迭代 007：受控运行 action 串行化需求文档

## 1. 目标

使 `ControlledRunner.run()` 对同一合法 idempotency key 实现跨进程的 at-most-once child launch，
同时保持已完成 effect 的幂等重放与异常后的可恢复执行。

## 2. 功能需求

### R1：相同 run action key 必须覆盖完整可变执行尾部

- action record 的读取、request-hash 冲突检查、token consume、session begin/resume、child launch、
  immutable result/report/action record 持久化及成功 session 收束，必须被同一个稳定 OS lock 串行化。
- 两个相同 key、相同 request 的并发 caller 中，只允许一个进入 child launch；等待者在锁释放后必须
  读取并验证相同 action record，返回完全相同的 persisted result，不得再次 consume 不同 effect 或
  再次启动 child。
- 同一 key 绑定不同 request 的 caller 仍返回 `BTAG-IDEMPOTENCY-CONFLICT`，且不能启动 child。

### R2：恢复、隔离与诊断

- lock 路径必须在 state-root `actions` 内稳定派生，release 时不删除；进程异常退出后后续 caller
  能获得 lock 并按现有 persisted-result/session resume 路径完成。
- 不同 action key 的 lock 互不阻塞；现有 run timeout（1–600 秒）和 child sandbox/配额语义不变。
- lock 的 open/prepare/acquire/release/close 失败都必须稳定映射为 `BTAG-RUN-ACTION-LOCK`，descriptor
  不泄漏。等待窗口须覆盖允许的 child timeout 与明确的完成余量，避免正常同 key caller在执行中错误
  地得到默认 30 秒 lock timeout。

### R3：兼容与范围

- 不增加运行时依赖，继续使用现有 Windows/POSIX lock abstraction，兼容 Python 3.8。
- run result、session journal、action-record schema、CLI 调用形状和 MIT 许可证不变。
- 不以吞掉 `BTAG-WRITE-EXISTS` 来伪造成功；幂等成功只来自读取并验证已持久化的同一 effect。

## 3. 非功能约束

- 仅修改 runner、测试、迭代文档和派生 manifest。
- 测试必须使用真实 `spawn` process，不能仅靠同进程 mock 锁断言。

## 4. 需求追踪

| 需求 | 主验收证据 |
| --- | --- |
| R1 | first worker child-start 后启动 second worker；最终精确一次 child start、双 caller 同 result |
| R2 | action lock path/诊断/descriptor、different-key isolation、crash-resume 回归 |
| R3 | run resume、完整三解释器、clean-wheel 与 independence audit |
