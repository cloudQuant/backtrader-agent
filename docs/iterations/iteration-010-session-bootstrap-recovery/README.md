# 迭代 010：会话创建引导恢复

第 002 轮已保证完整 session 的并发 transition、recover 和 create 线性化，但最终收敛审计发现了一个
创建过程的崩溃窗口：空 `journal.jsonl` 已安全创建、`manifest.json` 尚未创建时进程中断，后续同 ID 的
`session create` 把可恢复的空 journal 当作冲突，泄漏 `BTAG-WRITE-EXISTS`。

| 文档 | 用途 |
| --- | --- |
| [需求文档](requirements.md) | 定义安全重试与不安全残留的边界 |
| [设计文档](design.md) | 描述锁内 bootstrap 状态机 |
| [验收文档](acceptance.md) | 定义真实进程中断和发行门 |
| [实施计划](implementation-plan.md) | 测试先行的实现步骤 |

## 边界

- 仅修复本机 state root 中“空 journal、无 manifest”的可证明创建中断状态；不把任意残留目录或非空
  journal 自动当作安全状态。
- 不改变既有 session ID、hash chain、状态转换、锁路径或 CLI 形状；不引入依赖，继续兼容 Python 3.8、
  POSIX/Windows，MIT 许可证不变。
- 恢复仍只在 per-session stable lock 内进行；不扩大到网络文件系统或跨主机分布式锁语义。

## 验收结论

已通过。真实 `spawn` 子进程在 manifest publish 边界退出后，后续 `session create` 能复用已 fsync 的空
journal 并完成 NEW manifest；非空或 symlink journal 均稳定拒绝且不修改原对象。三解释器、静态检查、
独立性审计、doctor 与 clean-wheel 14-cell 矩阵均通过；完整证据见[验收文档](acceptance.md)。
