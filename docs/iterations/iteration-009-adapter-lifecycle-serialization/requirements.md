# 迭代 009：Adapter 生命周期串行化需求文档

## 1. 目标

让同一 adapter host 在同一 external target 上的 apply install/uninstall 成为跨进程线性化操作，避免
卸载竞争泄漏原始文件系统错误或出现未定义的文件/manifest 交错状态。

## 2. 功能需求

### R1：stable target/host lifecycle lock

- `install(..., apply=True)` 与 `uninstall(..., apply=True)` 必须共享同一稳定 lock，路径位于
  `<target>/.backtrader-agent/installer/<host>.lock`；release 时不删除。
- lock 覆盖 apply 分支的完整重新预检、文件/manifest 写入或校验、删除校验、unlink 与结果构造。
- 不同 host 的 lock 互不阻塞；同 host 的并发 apply 必须形成单一顺序，后到者只观察前者已提交的
  完整状态。

### R2：受控诊断与完整性

- open/prepare/acquire/release/close 失败稳定映射为 `BTAG-INSTALL-LOCK`，descriptor 无泄漏。
- 并发双 uninstall：恰一个 `uninstalled`；另一个在获锁后因 manifest 已不存在返回
  `BTAG-UNINSTALL-MANIFEST`，不得暴露 `FileNotFoundError` 或删除未知文件。
- install/uninstall 仍仅针对 manifest 列出的 hash 精确文件；用户修改或 manifest 冲突保持现有
  `BTAG-UNINSTALL-CONFLICT`/`BTAG-INSTALL-*`。

### R3：preview 与兼容性

- `apply=False` install/uninstall 不创建 `.backtrader-agent`、lock 或 adapter 文件，结果形状不变。
- 不增加依赖，兼容 Python 3.8、Windows/POSIX；MIT 许可证与 CLI 形状不变。

## 3. 非功能约束

- 仅修改 installer、测试、迭代文档和派生 manifest。
- 真实 spawn 测试必须在 target adapter unlink 边界同步，而不是只测试 `_locked` helper。

## 4. 需求追踪

| 需求 | 主验收证据 |
| --- | --- |
| R1 | same host two spawn uninstall、different-host lock isolation、apply lock path |
| R2 | old raw unlink race、fixed domain diagnostic、lock fault/descriptor tests |
| R3 | preview zero-write、existing installer/uninstall regression、发行矩阵 |
