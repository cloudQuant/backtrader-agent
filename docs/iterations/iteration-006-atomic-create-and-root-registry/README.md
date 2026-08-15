# 迭代 006：原子 create-only 与 RootRegistry 线性化

前五轮已分别收紧 session、token、change transaction 与 action key 的锁边界。进一步审计发现
两项共享底层缺口：`atomic_write(..., create_only=True)` 在第二次 `exists()` 检查后仍调用可覆盖
既有文件的 `os.replace()`；RootRegistry 的 `load → update → replace` 也没有跨进程锁。前者会让
两个不同 immutable payload 都“创建成功”且静默覆盖，后者会让两个不同 root 注册都返回成功但
最终 registry 丢失其中一个。

| 文档 | 用途 |
| --- | --- |
| [需求文档](requirements.md) | 原子 no-clobber 与 root registry 目标/边界 |
| [设计文档](design.md) | hard-link publish 与稳定 registry lock |
| [验收文档](acceptance.md) | 红测、诊断和发行门 |
| [实施计划](implementation-plan.md) | 测试先行的实现序列 |

## 已确认的缺口

- 当前 create-only 路径会在临时文件落盘后再次检查 destination，随后 `os.replace()`；两个进程
  可以都越过检查，后到者无条件覆盖先到者。
- `RootRegistry.register()` 对同一 `roots.json` 的两个不同 ID 并发注册没有锁；每个进程从旧
  snapshot 生成自己的完整文件，最后写入者丢弃另一项注册。
- 上层基于 create-only 的 immutable state 因而不能把“无错误返回”视为真实 no-clobber 保证。

## 边界

- 本轮保证本机同一文件系统中 helper 的 create-only 不覆盖既存 pathname，并让 RootRegistry
  的 register 操作线性化；不承诺网络文件系统或跨机器分布式一致性。
- 不改变 `atomic_write_*`、RootRegistry、CLI 或 schema 的公开调用形状；许可证保持 MIT。
- 各上层调用者对正常同内容竞争的用户体验（例如 re-read 后把 `BTAG-WRITE-EXISTS` 转为成功）
  在下一轮单独审计；本轮优先消除静默覆盖和 registry 丢失。

## 验收结论

已通过。真实 `spawn` worker 先复现了旧实现的“两者 success、后到者覆盖”与 registry 丢失
更新，再验证修复后 create-only 为恰一成功/一 `BTAG-WRITE-EXISTS`，两个不同 root 注册均被
保留。base、py38、py312、静态检查、独立性审计、doctor 与 clean-wheel 14-cell 矩阵均通过；
完整证据见[验收文档](acceptance.md)。
