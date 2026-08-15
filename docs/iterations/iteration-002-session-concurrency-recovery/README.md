# 迭代 002：会话并发与恢复契约

本迭代以迭代 001 已通过的可信执行契约为前提，修复 `SessionStore` 在多个本地进程同时操作同一会话时不能保持 journal 与 checkpoint 单一哈希链的问题。

| 文档 | 用途 |
| --- | --- |
| [需求文档](requirements.md) | 并发缺口、范围、非目标和可追溯需求 |
| [设计文档](design.md) | 锁文件、临界区、恢复及跨平台边界 |
| [验收文档](acceptance.md) | 多进程复现、回归和接受条件 |
| [实施计划](implementation-plan.md) | 测试先行的最小实施序列 |

## 已确认的缺口

当前 `SessionStore.transition()` 的顺序是 `load → 计算 sequence/event → append journal → 写 manifest`。单个 append 与单个 manifest replace 都具有自己的原子性，但整个读—改—写序列没有跨进程排他性。两个进程可从同一个 checkpoint 派生相同 `sequence=1` 与相同 `previous_event_hash`，使 journal 出现两个互相竞争的首事件，之后 `recover()` 只能保留一个有效前缀。

这不是“恢复功能正常工作”的证明：恢复截断竞争写入会丢失一个已返回给调用方的状态转换结果。因此本轮把每个 session 的 mutation/recovery 线性化，并以真实多进程测试作为验收证据。

## 边界

- 保持离线优先、标准库运行时、既有 session schema、状态图、hash 语义和恢复格式。
- 不试图把不同 session、不同 state root 或跨机器网络文件系统变成全局事务。
- 不改动策略渲染、审批、引擎/环境证明或发行版本；这些由迭代 001 覆盖。

## 验收中发现的发布工件修正

本轮首次全量验收发现 `manifest.json` 生成器会把 `.mypy_cache` 和
`.ruff_cache` 这类已忽略的本机工具缓存写入 source manifest。它会使同一
源码树因执行 lint/type-check 而产生不可复现的发布清单，也使 clean-wheel
复制工作区携带无关缓存。该问题阻塞本轮的 B6 发行门，因此纳入本迭代的
验收修正：先补充需求、设计和测试，再统一生成器、契约测试与 clean-copy
排除规则，最后重新生成清单。

## 验收结论

已通过。8 个 spawn 进程的同会话竞争只留下一个合法事件；并发 create、
recover/transition 竞争、不同会话锁、锁异常路径和临时缓存排除均有自动化
证据。base、Python 3.8、Python 3.12、Ruff、独立性审计、doctor 和 clean-wheel
14-cell matrix 全部通过。详细命令与结果见 [验收文档](acceptance.md)。
