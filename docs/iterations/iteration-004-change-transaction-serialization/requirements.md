# 迭代 004：变更事务跨进程序列化需求文档

## 1. 目标

让 `ChangeManager.apply()` 对同一已注册 target root 的提交、幂等返回和事务恢复具有跨进程
线性化语义：存活调用者的 transaction 不能被另一个调用者 rollback，且目标文件的 preimage
从检查到提交期间不被另一项受本组件管理的变更穿插修改。

## 2. 功能需求

### R1：稳定、隔离的 target-root OS 锁

- 每个合法 `target_root_id` 必须映射到 state root 内一个稳定的、不可由原始 ID 注入路径的
  lock file；同一 ID 始终使用同一文件，不同 ID 不得意外共享文件。
- 复用迭代 003 的跨平台稳定 OS 文件锁原语：退出时只释放 descriptor lock，不删除 lock file；
  崩溃后内核释放锁，后续进程可继续使用同一文件。
- 打开、准备、获取、释放和关闭失败必须转换为稳定的 `BTAG-CHANGE-LOCK`，不暴露原始 OS
  路径或异常；不得泄漏 descriptor。

### R2：同 root apply 临界区必须覆盖全部可变提交边界

- 在通过签名和 session/manifest 基础校验后，针对同一 target root 的临界区必须覆盖：
  action idempotency record 检查、token consume、draft/target 解析、preimage 检查、
  transaction 创建/恢复/应用、applied record、action record 和 applied session 提交。
- 同一 manifest + token + idempotency key 的竞争调用者必须在第一个提交后读取并验证同一
  action record，再返回同一结果；正常竞争不得泄露 `BTAG-WRITE-EXISTS`、rollback 或
  idempotency 冲突。
- 同一 target root 上的不同 manifest 必须以临界区顺序执行；后到者重新检查 preimage，不能
  将预览时已经过期的 preimage 静默写入。

### R3：存活事务不得被恢复路径误回滚

- 真实并发进程中，第二个 apply 不得在第一个仍处于 `APPLYING` 时进入
  `_prepare_transaction()` 的 rollback 分支。
- 当拥有锁的进程异常退出时，后续进程在获得 OS 锁后仍可按既有 `APPLYING` journal 规则
  rollback/recover，并保持 target 与 journal 的 hash 校验。

### R4：保持现有安全、状态机和锁顺序契约

- change manifest、bound record、token 签名/消费、transaction hash/preimage、action-record
  签名和 SessionStore hash chain 的现有语义不变。
- 基础 token/record 验证中的 secret lock 在进入 target-root lock 前完成并释放。临界区内的
  target-root lock 可分别短暂取得 approval lock（消费）或 secret lock（签名），两者都会在
  session lock 前释放；其他 API 不得在持有 approval/session/secret lock 时反向获取
  target-root lock。
- 保持现有单进程 rollback、crash-resume、幂等和 session 回归。

## 3. 非功能约束

- 仅使用 Python 3.8 标准库；不增加运行时依赖或修改可安装包的依赖面。
- 变更限于 ChangeManager、必要的测试、迭代文档和派生 distribution manifest。
- 所有 Python 验收都在用户的 Anaconda 环境中执行，并禁用第三方 pytest 自动加载。

## 4. 需求追踪

| 需求 | 主验收证据 |
| --- | --- |
| R1 | 根 ID lock-path 隔离、lock OS error/close 测试 |
| R2 | 两个 spawn 同 manifest apply、不同 manifest stale-preimage 测试 |
| R3 | 暂停第一个 `APPLYING` worker 后启动第二个 worker 的红绿测试、crash-recovery 回归 |
| R4 | 既有 change/token/session 回归与完整 base/3.8/3.12/clean-wheel 门 |
