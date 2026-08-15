# 迭代 004：变更事务跨进程序列化验收文档

## 1. 功能门

| ID | 验收项 | 通过条件 |
| --- | --- | --- |
| A1 | 同 manifest 存活 apply 竞争 | 两个真实 spawn worker 在一个 `APPLYING` 窗口重叠；均成功并返回同一结果，transaction 最终为 `COMMITTED`，target 为 source hash，无误 rollback |
| A2 | action/idempotency 线性化 | 只生成一个经签名 action record；后到同 key 调用验证缓存并完成 applied session 语义，不出现 `BTAG-WRITE-EXISTS` |
| A3 | 同 root 不同 manifest | 后到变更在重新检查时稳定得到 `BTAG-CHANGE-PREIMAGE`，不得覆盖先到变更或破坏其 transaction |
| A4 | root 隔离与 lock 诊断 | 不同 root 映射不同 lock path、可并行进入；注入 open/acquire/release 异常均为 `BTAG-CHANGE-LOCK`，descriptor 已关闭 |
| A5 | 崩溃恢复与既有回归 | 既有 `APPLYING` journal crash-resume/rollback、多文件提交、token 与 session 状态机测试保持通过 |

## 2. 发行门

| ID | 命令 | 通过条件 |
| --- | --- | --- |
| B1 | base `pytest tests` | 全部通过 |
| B2 | py38、py312 `pytest tests` | 全部通过 |
| B3 | `ruff check src/backtrader_agent tests scripts` | 通过 |
| B4 | `scripts/audit_independence.py`、`scripts/doctor.py` | audit passed；doctor 输出合法 |
| B5 | `scripts/run_acceptance.py` | clean wheel、14-cell matrix、crash-resume、repair 均通过 |
| B6 | `scripts/build_manifest.py` 后 distribution manifest audit | manifest 新鲜且不含本机缓存 |

## 3. 完成判定

必须先保留旧实现的 A1 红测证据。A1–A5 与 B1–B6 全部通过、记录实际 worker 数和最终
transaction/action/target 证据，才可标记本轮完成并继续下一轮审计。

## 4. 实际验收记录

### 4.1 红测与功能门（2026-08-02）

| 项 | 红测/旧行为 | 修复后结果 |
| --- | --- | --- |
| A1 | `test_live_apply_is_not_rolled_back_by_a_competing_process` 以两个真实 `spawn` worker 让 A 停在 `APPLYING`、再启动 B；旧实现中 B 调用 `_rollback_transaction()`，`live_rollback=True`，测试稳定失败于 `assert not live_rollback`。 | B 在 target-root lock 外等待；A 先提交，随后两个 worker 均 success、返回相同 applied hashes，journal 最终为 `COMMITTED`，target 等于 source hash。 |
| A2 | 同上红测会让并发者进入尚未完成的 action/transaction 尾部。 | 只存在一个签名 action record；后到者读取并验证缓存结果，未出现 `BTAG-WRITE-EXISTS`。 |
| A3 | 两个 session 在同一 root 的同一目标上分别 prepare，均基于初始 preimage。 | 第一个提交后，第二个稳定报告 `BTAG-CHANGE-PREIMAGE`；目标保持第一个 change 的 source hash。 |
| A4 | 之前没有 ChangeManager 边界的锁路径、隔离和诊断覆盖。 | 两个不同 root ID 的真实 spawn worker 同时跨越 barrier；lock paths 不同且均成功。注入 open、POSIX acquire 和 release 失败均报告 `BTAG-CHANGE-LOCK`；acquire/release 用例各确认 descriptor 已关闭。 |
| A5 | 既有多文件失败/恢复语义是必须保留的基线。 | `tests/test_change_concurrency.py` 与 `tests/test_tokens_changes_sessions.py` 联合 19 项通过，覆盖同 effect resume、multi-file rollback、token 消费和 session hash-chain 回归。 |

### 4.2 发行门

| 门 | 实际结果 |
| --- | --- |
| B1 | base 完整 `pytest tests`：89 项，退出码 0。 |
| B2 | Python 3.8 与 Python 3.12 完整 `pytest tests`：均退出码 0。 |
| B3 | `ruff check src/backtrader_agent tests scripts`：`All checks passed!` |
| B4 | `scripts/audit_independence.py`：6/6 checks passed；`scripts/doctor.py`：`status=ready`。未注册 engine root 时 `execution_ready=false` 是预期提示。 |
| B5 | `scripts/run_acceptance.py`：`status=passed`；clean install/origin probe 通过，14 个 matrix cell 全部 passed（`14 passed, 1 warning in 180.90s`），crash-resume 与 repair 均通过。 |
| B6 | `scripts/build_manifest.py`：root 106 files、package 43 files；distribution manifest contract 与 independence audit 均通过。 |

唯一 warning 来自 Backtrader 已弃用的 Quandl feed import，不影响离线验收。
