# 迭代 008：Immutable Record 并发重放

第 006 轮把 `create_only=True` 修成真正的 no-clobber，这是正确的安全基础；但继续审计发现
DatasetService、ArtifactRenderer、TokenAuthority bound record 与 AdapterInstaller 仍使用“先检查
不存在、再 create-only 写入”的调用模式。两个相同请求并发到达时，后到者会安全地收到
`BTAG-WRITE-EXISTS`，而不是验证赢家 bytes 后作为同一 immutable effect 成功返回。

| 文档 | 用途 |
| --- | --- |
| [需求文档](requirements.md) | exact-create-or-verify 行为与调用者范围 |
| [设计文档](design.md) | canonical helper、竞态重读与错误映射 |
| [验收文档](acceptance.md) | 四类真实 spawn 竞争与发行门 |
| [实施计划](implementation-plan.md) | 测试先行的实现路径 |

## 边界

- 本轮只将相同 canonical bytes 的正常并发重试转为幂等成功；不同 bytes、symlink 或非普通文件仍
  保持各调用者既有 conflict code，绝不覆盖。
- 不改变 create-only 原语本身、不把 mutable state 更新改成 compare-and-swap，也不为外部 target
  引入粗粒度锁。
- ReportRenderer 的低层 direct render 合同未宣称可重复调用；ControlledRunner 已有自己的 exact
  persist helper，因此不在本轮合并语义。

## 验收结论

已通过。四类真实 `spawn` worker 均先复现了旧调用者泄漏 `BTAG-WRITE-EXISTS`，修复后同内容的
竞争 caller 都通过 byte-exact 验证重放，而不同内容/symlink 仍被拒绝。base、py38、py312、静态
检查、独立性审计、doctor 与 clean-wheel 14-cell 矩阵均通过；完整证据见[验收文档](acceptance.md)。
