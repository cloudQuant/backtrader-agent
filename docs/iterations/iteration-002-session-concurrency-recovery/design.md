# 迭代 002：会话并发与恢复契约设计文档

## 1. 锁模型

每个 session 使用稳定的内部 lock file：

```text
<state-root>/session-locks/<session-id>.lock
```

`session-id` 在生成路径前由既有 `SESSION_RE` 校验。锁文件只承载 OS 锁，不写业务状态，不在 release 时删除；内核会在进程退出时释放 descriptor 关联的 advisory lock，保留路径避免并发创建者锁住不同 inode。

`SessionStore._locked(session_id)` 是 context manager：创建父目录与 lock file，获取排他 lock，`yield`，最后 unlock/close。POSIX 延迟导入 `fcntl` 并使用 `flock(LOCK_EX)`；Windows 延迟导入 `msvcrt`、确保第一个字节存在，并以有限重试获得一个字节锁。平台调用异常统一转换为 `BTAG-SESSION-LOCK`。

## 2. 临界区分层

公开方法只负责取得锁；内部 helper 不再递归取锁：

```text
create()     -> with _locked: _create_unlocked()
load()       -> with _locked: _load_unlocked()
transition() -> with _locked: _transition_unlocked()
recover()    -> with _locked: _recover_unlocked()
cancel()     -> with _locked: _load_unlocked() + _transition_unlocked()
archive()    -> with _locked: _load_unlocked() + _transition_unlocked()
```

`_transition_unlocked()` 保留现有的 hash chain 算法，但把 checkpoint read、合法 transition 校验、event 生成、append+fsync 和 manifest atomic replace 保持在同一个临界区。`_recover_unlocked()` 也在同一锁内调用 `_transition_unlocked()` 记录 `RUNNING → PAUSED`，杜绝恢复和普通 transition 相互覆盖。

## 3. 正确性不变量

对每个 session：

1. `journal.sequence` 连续为 `1..n`，每个 `previous_event_hash` 指向上一事件；
2. manifest `last_sequence=n`、`last_event_hash` 与 journal 尾一致，checkpoint hash 有效；
3. 任一成功 transition 的 event 处于该有效前缀，而不是 recover 后被隔离的尾部；
4. 同 session 操作线性化；不同 session 不共享 lock file。

崩溃发生在 journal append 与 checkpoint replace 之间时，既有 `recover()` 仍可根据有效 journal 重建 checkpoint；锁只防止并发调用将这一恢复机制当作常规冲突解决器。

## 4. 测试设计

测试模块增加 module-level worker，使用 `multiprocessing.get_context("spawn")` 与 `Barrier`，确保 Python 3.8/Windows 兼容并保证两个独立进程实际竞争。worker 将结果（success 或 `AgentError.code`）回传 parent；parent 验证唯一 journal sequence、hash chain、manifest 与无 `journal.corrupt.*`。

另有并发 create、recover/transition 排他和不同-session进度测试。所有 worker 以短超时收敛；失败时测试打印 exit code/queue 结果，而不是无限等待。

## 5. 发布清单的临时文件边界

`scripts/build_manifest.py` 的 root 排除集合继续列出 manifest 语义明确排除的
目录，并补充 `.mypy_cache` 与 `.ruff_cache`。它们已被仓库忽略，且会由本轮
要求的质量门生成，因此不能成为发布输入。`tests/test_distribution_contracts.py`
独立表达相同契约，并用临时 fixture 证明生成器忽略这两类目录；clean-wheel
复制也采用相同排除，避免临时缓存穿透到构建上下文。

这里不把“所有未跟踪文件”一概排除：本轮刚创建、尚未暂存的文档与源文件仍应
进入 manifest，确保清单继续发现真实交付物漏记。排除只覆盖明确的、工具生成
且不属于源分发的缓存目录。
