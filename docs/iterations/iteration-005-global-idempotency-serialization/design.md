# 迭代 005：全局幂等 action 序列化设计文档

## 1. action-key 锁命名

在 `ChangeManager` 新增稳定路径：

```text
<state-root>/change-action-locks/<sha256(idempotency-key)>.lock
```

路径首先通过 `_action_path()` 校验 key 格式，再仅以其 digest 构造文件名。它使用
`exclusive_file_lock()`，并在 ChangeManager 边界映射为 `BTAG-CHANGE-ACTION-LOCK`。锁文件
持久保留，避免 unlink/recreate 导致两个进程锁到不同 inode。

## 2. 临界区和顺序

`apply()` 的 manifest、prepared-record、session 和 token 签名基础验证仍在锁外完成。随后：

```text
action-key lock
  → target-root lock
    → action-record check / cached-result validation
    → token consume
    → transaction recovery or prepare / target apply
    → applied record / signed action record / session transition
```

所有 apply 都先取得 action-key lock，再取得 target-root lock，因此不会形成 `A→root1` 与
`root1→A` 的环。不同 key 不相互等待；不同 key 的同 root 请求仍在第二层 target-root lock
有序执行。对不同 request hash 的同 key 调用，先到者提交 action record，后到者在外层 key 锁
持有期间立即识别 request hash 不同，尚未消费 token、更未写 target。

## 3. 测试设计

- 两个真实 spawn worker 准备不同 root/不同 manifest，但使用同一个 idempotency key。worker A
  在 `APPLYING` 后、target replacement 前暂停；worker B 在旧实现中能跨越自己的 root lock 并
  到达 replacement。新实现中 B 被 action-key lock 阻塞，A 提交后 B 得到
  `BTAG-IDEMPOTENCY-CONFLICT`，B target 不存在且 token 仍为 ISSUED。
- 验证不同 action key、不同 root 的 worker 可同时跨越 barrier，且 action lock 文件路径不同。
- 注入 action lock open/acquire/release 错误并确认稳定 code、descriptor close；保留第 004
  同 root live-transaction 和全部 change/token/session 回归。
