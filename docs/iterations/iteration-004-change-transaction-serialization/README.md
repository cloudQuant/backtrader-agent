# 迭代 004：变更事务跨进程序列化

迭代 003 已让 approval 的发放和消费在单个 request 上具备可恢复的 OS 锁语义，
但 `ChangeManager.apply()` 在 approval 消费之后仍会并发读写同一 target root、同一
transaction journal 和 action record。两个进程在同一个已授权变更上重叠时，后到者可将
先到者仍处于 `APPLYING` 的事务误当成崩溃遗留事务并执行 rollback；不同变更也可同时通过
同一 target 的 preimage 校验后相互覆盖。

| 文档 | 用途 |
| --- | --- |
| [需求文档](requirements.md) | 风险、目标、范围与可追溯需求 |
| [设计文档](design.md) | 每个 target root 的稳定锁、临界区和锁顺序 |
| [验收文档](acceptance.md) | 红测、多进程、恢复和发行门 |
| [实施计划](implementation-plan.md) | 测试先行的实现序列 |

## 已确认的缺口

- `apply()` 在检查 action idempotency record、消费 token、创建/恢复 transaction、写 target
  和写回 action record 之间没有覆盖 target root 的跨进程锁。
- `_prepare_transaction()` 对遗留 `APPLYING` journal 的 rollback 是正确的崩溃恢复路径，
  但它无法区分另一个仍存活进程的执行；当前调用边界使这条恢复路径可被并发调用者误触发。
- 同一 root 上的两个不同 manifest 可以各自读到相同 preimage，再先后写入，令“预览时的
  preimage”不再是提交时的可靠保护。

## 边界

- 只保证同一台机器、同一 private state root 下的本机进程互斥；不承诺跨机器或网络文件
  系统上的分布式协调。
- 不改变 change manifest、token、transaction、action-record 或 CLI 的公开 schema；许可证
  继续为 MIT。
- 不把审批锁扩大为长时间 target-root 锁；授权状态和文件提交保持各自明确的职责与恢复边界。

## 验收结论

已通过。真实 `spawn` 红测证明旧实现会让第二个进程进入第一个仍存活事务的 rollback 路径；
按 target root 的稳定 OS 锁使两个相同 apply 顺序完成并返回同一结果。base、Python 3.8、
Python 3.12、Ruff、独立性审计、doctor 与 clean-wheel 14-cell matrix 均通过。详见
[验收文档](acceptance.md)。
