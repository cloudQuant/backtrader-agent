# 迭代 005：全局幂等 action 序列化验收文档

## 1. 功能门

| ID | 验收项 | 通过条件 |
| --- | --- | --- |
| A1 | 跨 root 同 key 红绿 | 两个真实 spawn worker；旧实现能让第二个 worker 到达 target replacement。修复后第二个 worker 在 A 提交后稳定得到 `BTAG-IDEMPOTENCY-CONFLICT`，第二目标保持不存在、第二 token 仍 ISSUED。 |
| A2 | 同 key 同请求 | 竞争调用返回同一签名 action result，只有一个 action record 和一次 target effect。 |
| A3 | action-key 隔离与诊断 | 不同 key 映射不同文件、不同 root 可并发进入；open/acquire/release 失败为 `BTAG-CHANGE-ACTION-LOCK` 且 descriptor 关闭。 |
| A4 | 迭代 004 与状态机回归 | 同 root live transaction、multi-file rollback/resume、token 消费、session hash chain 均通过。 |

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

必须保留 A1 的旧实现红测证据。A1–A4 与 B1–B6 全部通过、记录真实 worker 结果与 action/
target/token 副作用，才可标记本轮完成。

## 4. 实际验收记录

### 4.1 红测与功能门（2026-08-02）

| 项 | 红测/旧行为 | 修复后结果 |
| --- | --- | --- |
| A1 | 两个真实 `spawn` worker 在 left/right 两个不同 root、不同 manifest/token 上复用 `global-key-race`。A 停在 `APPLYING` 后，旧实现中 B 到达自己的 `_replace_target()`，`reached_second_target=True`，稳定失败于 `assert not reached_second_target`。 | A 在 action-key lock 内提交；B 随后得到 `BTAG-IDEMPOTENCY-CONFLICT`，从未到达 replacement。left target 为已签名 source hash，right target 不存在，B 的 approval record 仍为 `ISSUED`。 |
| A2 | 没有 state-wide action serialization 时，同 key 的副作用边界依赖相互独立的 target lock。 | `test_live_apply_is_not_rolled_back_by_a_competing_process` 保持两个同请求 worker success、相同 result、一个 action record 和一个 committed target effect。 |
| A3 | 缺少 ChangeManager action-key lock 的隔离与调用边界诊断。 | 不同 key 的两个真实 worker 可同时跨 barrier，路径不同且均成功；注入 open、POSIX acquire、release 错误均返回 `BTAG-CHANGE-ACTION-LOCK`，acquire/release 均确认 descriptor 已关闭。 |
| A4 | 第 004 轮 target-root transaction 的恢复和 session/token 语义必须保持。 | `tests/test_change_concurrency.py` 与 `tests/test_tokens_changes_sessions.py` 联合 24 项通过，覆盖 same-root live transaction、multi-file rollback/resume、token 与 session hash-chain。 |

### 4.2 发行门

| 门 | 实际结果 |
| --- | --- |
| B1 | base 完整 `pytest tests`：94 项，退出码 0。 |
| B2 | Python 3.8 与 Python 3.12 完整 `pytest tests`：均退出码 0。 |
| B3 | `ruff check src/backtrader_agent tests scripts`：`All checks passed!` |
| B4 | `scripts/audit_independence.py`：6/6 checks passed；`scripts/doctor.py`：`status=ready`。未注册 engine root 时 `execution_ready=false` 为预期提示。 |
| B5 | `scripts/run_acceptance.py`：`status=passed`；clean install/origin probe 通过，14 个 matrix cell 全部 passed（`14 passed, 1 warning in 177.19s`），crash-resume 与 repair 均通过。 |
| B6 | `scripts/build_manifest.py`：root 111 files、package 43 files；distribution manifest contract 与 independence audit 均通过。 |

唯一 warning 来自 Backtrader 已弃用的 Quandl feed import，不影响离线验收。
