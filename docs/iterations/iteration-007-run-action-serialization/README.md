# 迭代 007：受控运行 action 串行化

第 005 轮已为 change apply 提供全局 idempotency-key lock，第 006 轮又让 immutable publish
具有真实 no-clobber 语义。对 `ControlledRunner.run()` 的继续审计发现，同一 run key 的 action
record 在子进程结束后才写入：第二个进程若在第一个进程已把 session 置为 `RUNNING`、但 action
record 尚未存在时进入，会被 `_begin_or_resume_session()` 当作可恢复执行，并可能再次启动受控
子进程。后续 immutable result 写入会检测冲突，但不能撤销已经重复发生的执行。

| 文档 | 用途 |
| --- | --- |
| [需求文档](requirements.md) | 同 key 的单次执行与恢复边界 |
| [设计文档](design.md) | 稳定 action lock、锁范围与超时策略 |
| [验收文档](acceptance.md) | 真实 spawn child-start 红测及发行门 |
| [实施计划](implementation-plan.md) | 测试先行的实现路径 |

## 边界

- 本轮仅串行化相同 run idempotency key 的 mutable execution tail；不同 key 不被全局串行化。
- 已有 crash-resume 语义必须保留：持锁进程异常退出后 OS lock 自动释放，后续同 key 可继续完成
  已落盘的 effect。
- 本轮不调整数据、draft、installer 等上层 immutable record 的同内容重读体验；这些是独立的
  下一轮审计对象。

## 验收结论

已通过。真实 `spawn` worker 先复现旧实现的两次 child start；修复后第二个同 key worker 在第一个
完成前不能进入 child boundary，随后验证并重放同一个 persisted result。base、py38、py312、静态
检查、独立性审计、doctor 与 clean-wheel 14-cell 矩阵均通过；完整证据见[验收文档](acceptance.md)。
