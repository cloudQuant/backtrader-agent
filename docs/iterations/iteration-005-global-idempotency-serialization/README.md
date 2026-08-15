# 迭代 005：全局幂等 action 序列化

迭代 004 已按 target root 串行化 change transaction，因此同一 root 的并发提交不再互相
rollback。但 `idempotency_key` 对应的是 state root 下全局唯一的 action record，而不同 root
各自持有的 target-root lock 并不互斥。两个不同 root 的请求若误复用同一个 key，会分别写入
目标文件后才竞争同一 action record，破坏“一个 key 只代表一个请求”的契约。

| 文档 | 用途 |
| --- | --- |
| [需求文档](requirements.md) | 全局幂等语义、范围与追溯需求 |
| [设计文档](design.md) | action-key 锁、与 target-root 锁的顺序 |
| [验收文档](acceptance.md) | 跨 root 红测、诊断与发行门 |
| [实施计划](implementation-plan.md) | 测试先行的实现序列 |

## 已确认的缺口

- `ChangeManager._action_path()` 以 idempotency key 的 hash 在全局 `actions/` 下命名，
  但目前只有 `change-locks/<target-root>.lock`。
- 两个不同 root 的请求可同时看到 action record 不存在、各自 consume token 并写 target；
  随后 create-only action write 至多只能保留一个记录，另一方已经产生不应发生的副作用。
- 这不是 target 文件锁能解决的问题：冲突域是全局 idempotency key，必须在进入每个
  target-root 临界区之前先线性化。

## 边界

- 只覆盖 ChangeManager apply 的本机同一 state root 全局 key 语义，不改变 run action 的
  独立 key namespace，也不承诺跨机器分布式协调。
- 不改变 action-record schema、token schema、CLI 输入输出或 MIT 许可证。
- 本轮不把不同 key 的不同 root 请求串行化；它们应继续拥有并行能力。

## 验收结论

已通过。真实 `spawn` 红测证明旧实现会让复用同一个 key 的跨 root 第二请求到达 target
replacement；action-key 锁后它在任何 target/token 副作用前稳定得到 idempotency conflict。
base、Python 3.8、Python 3.12、Ruff、独立性审计、doctor 与 clean-wheel 14-cell matrix
均通过。详见 [验收文档](acceptance.md)。
