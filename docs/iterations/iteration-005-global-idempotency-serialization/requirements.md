# 迭代 005：全局幂等 action 序列化需求文档

## 1. 目标

使 `ChangeManager.apply()` 的 idempotency key 在同一 private state root 中真正线性化，无论
请求所写入的 target root 是否相同。一个 key 要么返回其已签名的同一结果，要么在副作用发生
前稳定拒绝与该 key 不同的请求。

## 2. 功能需求

### R1：提供稳定的 action-key OS 锁

- 合法 idempotency key 必须映射到 state root 内稳定、由 SHA-256 digest 命名的 lock file；
  不得把原始 key 作为路径片段，也不得在 release 时删除文件。
- 使用现有跨平台 shared file-lock 原语；打开、准备、获取、释放和关闭失败必须映射为稳定
  `BTAG-CHANGE-ACTION-LOCK`，不泄漏路径或原始 OS 异常。
- 不同 key 映射不同 lock；异常退出后内核释放锁，后续调用可复用同一文件。

### R2：action lock 必须先于 target-root lock 覆盖可变 apply 边界

- 在完成纯验证后，按 `action-key lock → target-root lock` 的固定顺序覆盖 action record
  检查、token consume、transaction、target 写入、applied record、action record 和 session 提交。
- 同 key、同请求的竞争者必须读取并验证缓存 action result，返回同一结果；不得再次执行
  target mutation 或产生 `BTAG-WRITE-EXISTS`。
- 同 key、不同 request hash（包括不同 root/manifest/token）的后到者必须在 token consume、
  transaction 创建或 target replacement 前报告 `BTAG-IDEMPOTENCY-CONFLICT`。

### R3：保持并行性与既有恢复语义

- 不同 key 且不同 target root 可以独立进入其各自临界区；不同 key 同 root 仍由迭代 004
  的 target-root lock 串行。
- 不改变 transaction crash recovery、action-record 签名、token/session 状态机或 `BTAG-CHANGE-LOCK`
  的既有 target-root 锁契约。

## 3. 非功能约束

- Python 3.8 标准库实现，不增加运行时依赖。
- 变更限于 ChangeManager、对应测试、迭代文档与派生 manifest。
- 所有 Python 验收在用户的 Anaconda 环境中运行，并禁用外部 pytest 自动加载。

## 4. 需求追踪

| 需求 | 主验收证据 |
| --- | --- |
| R1 | key lock path 隔离、OS error/descriptor close 测试 |
| R2 | 两个 spawn、不同 root/同 key 的暂停 target replacement 红绿测试 |
| R3 | 不同 key/不同 root barrier、迭代 004 transaction 和完整发行回归 |
