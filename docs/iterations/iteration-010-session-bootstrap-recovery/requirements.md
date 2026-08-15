# 迭代 010：会话创建引导恢复需求文档

## 1. 目标

令 `SessionStore.create(session_id)` 在进程于首次创建的 journal 与 manifest 之间中断后可安全重试，
同时拒绝任何不能证明为无事件、无 manifest 的残留状态。

## 2. 功能需求

### R1：空 journal bootstrap 可恢复

- 在取得既有 `<state-root>/session-locks/<session-id>.lock` 后，若 `manifest.json` 不存在而
  `journal.jsonl` 是普通、非 symlink 的空文件，`create()` 必须复用该 journal 并原子创建正常 NEW
  manifest。
- 同 ID 后续 `create()` 必须返回相同合法 manifest；创建完成后 journal 仍为空、`last_sequence=0`、
  checkpoint hash 有效。
- 真实子进程在 manifest publish 边界直接终止后，父进程重试必须满足上述契约，而不是返回
  `BTAG-WRITE-EXISTS` 或原始 OSError。

### R2：不安全 bootstrap 残留必须保守拒绝

- 无 manifest 的非空 journal、symlink journal 或其他非普通 journal 不得被清空、覆盖或解释为合法
  session；返回稳定 `BTAG-SESSION-BOOTSTRAP`。
- 遇到普通不存在 journal 时维持既有创建流程。存在 manifest 时维持现有幂等返回或冲突语义。
- 拒绝路径不得修改原 journal；不存在 silent repair 或数据丢失。

### R3：并发、兼容与发行契约

- 复用第 002 轮已有 per-session lock，不能新增全局串行化；不同 session 仍能并行。
- 不增加第三方依赖，不改 CLI/API 形状或 MIT 许可证，兼容 Python 3.8、Windows/POSIX。
- 更新派生 source/package manifests；完整测试、lint、独立性审计、doctor 和 clean-wheel 验收继续通过。

## 3. 非功能约束

- 只修改 `sessions.py`、会话测试、本轮文档和派生 manifests。
- 主功能证据必须是 `spawn` 子进程在实际 manifest publish 边界退出，而不是仅在单进程中 mock 锁。

## 4. 需求追踪

| 需求 | 主验收证据 |
| --- | --- |
| R1 | manifest publish 边界 `os._exit()` 后重试 create，检查 journal/manifest/checkpoint |
| R2 | 非空、symlink journal 拒绝且原字节不变 |
| R3 | 既有 create/transition/recover 并发回归、三解释器、clean-wheel 与 manifests |
