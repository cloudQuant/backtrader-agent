# 迭代 002：会话并发与恢复契约需求文档

## 1. 背景

`SessionStore` 是 data、spec、draft、validation、change、approval、run、repair 的共同状态边界。现有 journal append 与 manifest atomic replace 均不足以防止两个进程同时读取相同 checkpoint 后各自提交事件。结果可以是重复 sequence、分叉的 `previous_event_hash` 和随后恢复时的静默截断。

## 2. 目标

让同一 `state_root/session_id` 的 create、load、transition、recover、cancel、archive 在本机多进程情况下形成单一可验证顺序；每个成功返回的 transition 必须永久存在于最终有效 journal 前缀与 checkpoint 中。

## 3. 需求

### R1：每个会话必须有跨进程排他锁

- 锁的粒度是单一 session，不能把不同 session 的正常操作串行化。
- 锁文件在 state root 的专用内部目录中，名称仅由经过 `SESSION_RE` 校验的 session ID 派生；不得随释放删除，避免 inode replacement/stale-handle 竞争。
- POSIX 使用内核 advisory file lock；Windows 使用等价标准库锁。不得增加第三方依赖。
- 锁获取/释放失败必须报告稳定 `BTAG-SESSION-LOCK` 诊断；文件描述符不得泄漏。

### R2：所有 session 读写状态操作在一致临界区内执行

- `create` 必须在锁内检查/创建 journal 与 manifest，两个并发同 ID create 只能产生一个合法 NEW session，并保持幂等返回。
- `transition` 必须在锁内完成 checkpoint 校验、合法状态检查、sequence/event hash 计算、journal append、checkpoint 写入。
- `recover` 必须在锁内完成 journal 解析、尾部隔离、checkpoint 重建和 RUNNING→PAUSED 恢复事件；它不得与 transition 交叉覆盖。
- `load`、`cancel`、`archive` 也必须避免读取/使用一个正在写入的 session checkpoint。

### R3：既有持久化与恢复语义不可退化

- journal 仍是 append-only canonical JSONL；manifest 的 schema、`state_revision`、`last_sequence`、`last_event_hash` 与 checkpoint hash 定义保持兼容。
- 非法状态转换、损坏 journal 前缀隔离、RUNNING recovery、terminal 限制和列表行为必须保留。
- 锁只保护同一 session；不同 session 的并发 transition 仍可完成。

### R4：以真实多进程行为验收

- 测试必须由两个独立解释器/进程在屏障后竞争同一 session transition，而不是仅 mock 一个 Python mutex。
- 结果必须恰有一个合法状态推进；另一个调用清楚失败为 state-transition 冲突，或在其合法后继状态上产生线性化事件；无重复 sequence、无 hash 分叉、无需要恢复才能掩盖的尾部。
- 同时验证并发 create 的幂等性、recover/transition 互斥及不同 session 不互相阻塞。

### R5：发行清单不得吸收本机工具缓存

- root `manifest.json` 表示可分发源码，而不是当前开发机的临时目录；执行
  mypy 或 Ruff 后产生的 `.mypy_cache`、`.ruff_cache` 必须始终排除。
- manifest 生成器、source-manifest 契约测试和 clean-wheel 的源码复制必须
  使用同一组可审计的排除语义；缓存变化不能导致已提交的 manifest 漂移。
- 正常的受控源码、资源和本轮文档仍必须被清单覆盖；不能通过扩大排除范围
  掩盖真实源文件遗漏。

## 4. 非功能约束

- 保持 Python `>=3.8`；生产代码只用标准库。
- 不使用 busy-wait、进程全局锁、线程锁替代进程锁，也不依赖锁文件删除来释放。
- 锁的正常竞争应阻塞并线性化，而不是把临时竞争报告为业务失败；测试可使用有限超时来避免死锁。
- 变更限于 session store、测试、文档、CI/manifest（若需要）及验证证据。

## 5. 需求到验收追踪

| 需求 | 主验收证据 |
| --- | --- |
| R1 | 多进程同 session lock test；锁目录/异常路径测试 |
| R2 | 并发 transition/create/recover 实测；最终 journal/checkpoint 检查 |
| R3 | 既有 `test_tokens_changes_sessions.py` 与 `test_run_resume.py` 回归 |
| R4 | Python 3.8/3.12 完整套件与 clean-wheel acceptance |
| R5 | 临时缓存 fixture、manifest 覆盖测试、重新生成后的 clean-wheel 验收 |
