# 迭代 003：TokenAuthority 并发锁与恢复

迭代 002 已将 session journal/checkpoint 线性化，但 `TokenAuthority` 仍有两条
独立的本地持久化临界区：token secret 首次初始化，以及 approval request 的发放/
消费。当前实现分别依赖“先检查再 create-only 写入”和“`O_EXCL` 创建、结束时删除
锁文件”，无法在多进程和异常退出后保持同一恢复语义。

| 文档 | 用途 |
| --- | --- |
| [需求文档](requirements.md) | 风险、目标、范围与可追溯需求 |
| [设计文档](design.md) | 共享 OS 锁、迁移和锁顺序 |
| [验收文档](acceptance.md) | 红测、多进程和发行门 |
| [实施计划](implementation-plan.md) | 测试先行的实现序列 |

## 已确认的缺口

- `_secret()` 是 `exists → atomic_write(create_only)`，多个首次调用者可能让其中
  一个报 `BTAG-WRITE-EXISTS`，而不是读取最终写入的同一 32-byte secret。
- `_approval_lock()` 在异常退出后会留下 `<request-id>.lock`，后续任何 grant 或
  consume 均永久得到 `BTAG-APPROVAL-BUSY`；正常竞争者也会立即业务失败而非等待
  已持锁者完成。
- session lock 已有成熟的 OS advisory-lock 语义；若 TokenAuthority 另行维护
  不同机制，跨平台错误处理和恢复行为会继续漂移。

## 边界

- 只处理本机同一 state root 的跨进程互斥与意外退出恢复，不承诺跨机器/网络文件
  系统的分布式锁。
- 不改变 token payload、approval request schema、TTL、签名或权限规则。
- 不删除遗留 approval `.lock` 文件；升级后它们应作为可复用的稳定 lock inode，
  而不是永久阻塞物。

## 验收结论

已通过。真实 spawn 竞争证明旧实现会让 secret bootstrap 失败、将遗留 approval
lock 判为永久 busy；共享稳定 OS 锁后，8 个首次 secret 调用全部读取相同字节，两个
approval 竞争者均可顺序完成。base、Python 3.8、Python 3.12、Ruff、独立性审计、
doctor 与 clean-wheel matrix 均通过。详见 [验收文档](acceptance.md)。
