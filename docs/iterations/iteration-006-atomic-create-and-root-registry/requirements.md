# 迭代 006：原子 create-only 与 RootRegistry 线性化需求文档

## 1. 目标

让所有使用 canonical `create_only=True` 的持久化调用获得真实的 no-clobber 基础语义，并让
RootRegistry 的多 root 注册成为跨进程线性化的 read-modify-write 操作。

## 2. 功能需求

### R1：create-only publish 必须原子地拒绝既存路径

- `atomic_write_bytes(..., create_only=True)` 必须保证：竞争进程中至多一个调用者成功发布；
  其他调用者稳定得到 `BTAG-WRITE-EXISTS`，不得覆盖或替换成功调用者的 bytes。
- 成功发布必须仍使用同目录临时文件、文件 fsync 与目录持久化步骤；失败竞争者的临时文件必须
  清理，目标内容只能是某一个完整 payload，不能出现截断或混合 bytes。
- `create_only=False` 的 replace/upsert 语义保持不变；`atomic_write_json` 自动继承 bytes helper
  的行为而不改变 canonical JSON 格式。

### R2：RootRegistry register 必须跨进程保留所有独立注册

- RootRegistry 必须使用 state-root 内稳定的 OS lock 文件覆盖 load、冲突检查、update 与 write。
- 两个不同合法 ID 的并发 register 都成功后，最终 `roots.json` 必须含两条准确记录；同 ID/同
  record 保持幂等，同 ID/不同 record 保持 `BTAG-ROOT-CONFLICT`。
- registry lock 的打开、准备、获取、释放和关闭失败必须映射为 `BTAG-ROOT-LOCK`，descriptor
  无泄漏、文件不在 release 时删除、异常退出可恢复。

### R3：兼容性与范围控制

- 不增加运行时依赖，兼容 Python 3.8 和 Windows/POSIX 支持策略。
- RootRegistry list/get/resolve 的公开返回、根路径安全检查及现有 session/change/token contract
  保持不变。
- 本轮不把任何上层正常竞争错误静默吞掉；上层是否将相同 immutable payload 转成 idempotent
  success 由后续单独验收，避免在基础原语变更时扩大语义范围。

## 3. 非功能约束

- 仅修改 canonical persistence helper、RootRegistry、测试、迭代文档和派生 manifest。
- 所有 Python 验收在用户的 Anaconda 环境中运行，并禁用第三方 pytest 自动加载。

## 4. 需求追踪

| 需求 | 主验收证据 |
| --- | --- |
| R1 | publish-barrier 真实 spawn 竞争、bytes/JSON 完整性和临时文件清理 |
| R2 | 不同 root ID spawn race、同 ID 冲突/幂等、root lock error/close 测试 |
| R3 | 现有 data/scaffold/change/run、三解释器和 clean-wheel 完整回归 |
