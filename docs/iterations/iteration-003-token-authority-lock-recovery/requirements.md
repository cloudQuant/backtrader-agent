# 迭代 003：TokenAuthority 并发锁与恢复需求文档

## 1. 目标

让 TokenAuthority 的 secret 初始化和 approval 状态变更与 SessionStore 一样具备
可恢复、可诊断、跨进程线性化的本机持久化语义，并消除两套锁实现的行为漂移。

## 2. 功能需求

### R1：提供共享的稳定 OS 文件锁原语

- 生产代码只使用 Python 3.8 标准库：POSIX advisory file lock 和 Windows 等价
  字节锁；必须带有限重试，不得 busy-wait。
- 锁文件路径稳定且 release 时不删除；进程异常退出后内核释放 lock，后续调用能
  复用同一 inode。
- 打开、准备、获取、释放和关闭失败均转为调用方指定的稳定 `BTAG-*` 诊断；文件
  descriptor 无泄漏。
- SessionStore 必须迁移到该原语，但保留既有 `BTAG-SESSION-LOCK` 对外契约。

### R2：secret 首次初始化必须跨进程幂等

- 任意数量的同 state root 首次调用者最终读取完全相同、长度 32 bytes 的 secret，
  不得把正常竞争暴露为 `BTAG-WRITE-EXISTS`。
- 已存在的合法 secret 保持不变；长度非法仍报告 `BTAG-TOKEN-SECRET`。
- secret 初始化 lock 的路径不携带用户输入，权限不宽于现有私有 state root 约定。

### R3：approval lock 必须可等待、可恢复、按 request 隔离

- `grant_approval()` 和 `consume()` 对同一 request ID 在一个可验证临界区内完成；
  正常竞争先等待，随后从最新 request 状态得出 ISSUED/CONSUMED/EXPIRED 等业务结果，
  而非临时 `BTAG-APPROVAL-BUSY`。
- 旧版本崩溃留下的 `<approval-root>/<request-id>.lock` 不能永久阻断新版本；新实现
  必须能获取 OS lock 并复用该路径，且不得在 release 时 unlink。
- 不同 request ID 不应共享同一 lock。

### R4：保持状态机和锁顺序安全

- approval request、token 签名、消费幂等、RUN_APPROVED transition 和现有 session
  hash chain 语义保持不变。
- 不得引入 approval/session 或 secret/approval 的循环等待；锁顺序须明确，并由
  多进程测试覆盖正常竞争。

## 3. 非功能约束

- 不增加运行时依赖，不改 API/CLI 的可见输入输出 schema。
- 变更限于锁原语、SessionStore/TokenAuthority、测试、文档和派生 manifest。
- 所有 Python 验收使用 Anaconda 环境，并禁用外部 pytest 自动加载。

## 4. 需求追踪

| 需求 | 主验收证据 |
| --- | --- |
| R1 | 锁错误注入、descriptor close 检查、SessionStore 回归 |
| R2 | 8 个 spawn worker 同时 bootstrap secret，全部成功且字节一致 |
| R3 | 遗留 lock fixture、2 个 spawn approval lock worker、不同 ID lock path |
| R4 | grant/consume/session 回归、完整 base/3.8/3.12/clean-wheel 门 |
