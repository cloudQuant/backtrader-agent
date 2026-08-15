# 迭代 009：Adapter 生命周期串行化

第 008 轮已使并发相同 install 的 immutable 文件重放安全；但 AdapterInstaller 的 apply 生命周期仍没有
每个 target/host 的锁。并发 `uninstall --apply` 可以让两个进程都根据同一个 install manifest 计划
删除，后到者在 `Path.unlink()` 得到原始 `FileNotFoundError`。install 与 uninstall 也可在同一 manifest
和 adapter 文件集合上交错，缺少明确线性化边界。

| 文档 | 用途 |
| --- | --- |
| [需求文档](requirements.md) | apply 生命周期串行化及诊断契约 |
| [设计文档](design.md) | target/host stable lock 与 preview 边界 |
| [验收文档](acceptance.md) | 真实 spawn uninstall race 与发行门 |
| [实施计划](implementation-plan.md) | 测试先行的实现路径 |

## 边界

- 本轮只锁 `apply=True` 的 install/uninstall；preview 必须保持不创建锁文件、不改变外部 target。
- lock 按 target 和 host 隔离，不阻塞不同 host；不把外部 host registration 或用户手动命令变成自动
  副作用。
- 不改变 manifest 的 file hash 校验或拒绝删除用户修改文件的安全语义。

## 验收结论

已通过。真实 `spawn` worker 先复现了旧实现的 raw `FileNotFoundError`，修复后同 host apply lifecycle
被稳定锁串行化，竞争 caller 返回 `BTAG-UNINSTALL-MANIFEST`。base、py38、py312、静态检查、独立性
审计、doctor 与 clean-wheel 14-cell 矩阵均通过；完整证据见[验收文档](acceptance.md)。
