# 迭代 004：变更事务跨进程序列化设计文档

## 1. 锁命名与原语

在 `ChangeManager` 中新增逻辑锁路径：

```text
<state-root>/change-locks/<sha256(target_root_id)>.lock
```

`target_root_id` 已由 RootRegistry 校验，但仍只以 SHA-256 digest 构造文件名，避免把任何
外部字符串当作路径片段。路径通过已存在的 `exclusive_file_lock()` 获取，其保持 inode、有限
重试、跨平台 advisory locking 和调用边界的 `BTAG-CHANGE-LOCK` 错误映射。

## 2. apply 临界区

`apply()` 保留 manifest hash、prepared record、session 和 token 签名的只读基础校验。在取到
target-root lock 后，委托一个未再次加锁的私有路径完成现有可变尾部：

```text
action record check
  → token consume
  → resolve signed draft and targets
  → preimage check / transaction recovery or prepare
  → apply transaction
  → write applied record and signed action record
  → transition / validate applied session
```

这使 action record 成为同一 root 上第二个竞争者可见的线性化结果；第二个调用者不再碰到
第一个调用者的 `APPLYING` transaction。事务 journal 若在拿锁前已经由崩溃进程留下，持锁
调用者仍按现有 `_prepare_transaction()` 回滚并重新准备，保证恢复只针对不再存活的调用。

## 3. 锁顺序与范围

基础 manifest/token 验证所需的 secret lock 在进入 target-root lock 前完成并释放。持有
target-root lock 时，`authority.consume()` 短暂取得 approval request lock；读取/写入已签名
record 时可短暂取得 secret lock。两者均在 `_ensure_applied_session()` 取得 session lock 前释放。
有效偏序为：

```text
secret（基础验证后释放）
target-root → approval（短暂后释放） → session
target-root → secret（短暂后释放）   → session
```

当前 grant/consume、SessionStore 和其他 API 不会在持有 approval/session/secret lock 时反向
请求 target-root lock，因此不存在环。锁覆盖同一个 root 的所有 managed target，不只是单个
file：这样不同 manifest 即使改动重叠路径，也会在第二次 preimage 校验前顺序化。

## 4. 测试设计

- 用两个真实 `multiprocessing` spawn worker 复现：worker A 在 transaction 写为 `APPLYING`
  后、替换 target 前暂停；worker B 同时 apply。同一锁缺失时 B 进入恢复路径并 rollback A；
  新实现下 B 在 target-root lock 外等待，A 先提交，B 随后验证并返回相同 action result。
- 验证最终 transaction 为 `COMMITTED`、target hash 为 source hash、action record 只有一个、
  两个成功结果相同，且没有存活事务被标为 `ROLLED_BACK`。
- 验证不同 root 的 lock path 独立、同 root 不同 manifest 的后到者报告稳定 preimage 失效，
  并注入 lock OS error 证明 `BTAG-CHANGE-LOCK` 与 descriptor 关闭。
- 既有 change 多文件 rollback/crash-resume、token/session 回归继续运行，确保只收紧并发边界
  而非改变业务状态机。
