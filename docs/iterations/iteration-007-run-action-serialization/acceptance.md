# 迭代 007：受控运行 action 串行化验收文档

## 1. 功能门

| ID | 验收项 | 通过条件 |
| --- | --- | --- |
| A1 | 同 key single child launch | 真实 spawn：第一 worker 已到 child boundary 时，第二同 key worker 在第一完成前不能进入；最终 child-start 恰为 1，双 caller 结果完全相同。 |
| A2 | action record/replay | 等待者在 lock 后读取、验证并重放同一 persisted result；不同 request 同 key 为 `BTAG-IDEMPOTENCY-CONFLICT` 且未启动 child。 |
| A3 | lock 隔离与诊断 | 不同 key 可独立持锁；stable lock file 保留；open/acquire/release/close 失败为 `BTAG-RUN-ACTION-LOCK` 且 descriptor 关闭。 |
| A4 | 恢复兼容 | partial report + paused session 的现有 resume 测试通过；同 key retry 的 run/session state 合法完成。 |

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

必须保留 A1 的旧实现红测证据。A1–A4 和 B1–B6 均通过、并记录真实 child-start count 与 clean-wheel
结果后，才可标记本轮完成。

## 4. 实际验收记录

### 4.1 旧实现红测

- A1：第一个真实 `spawn` worker 已启动实际受控 Python child 并在 release 文件前阻塞；启动第二个
  相同 key worker 后，旧实现又启动第二个 child。断言实际得到 `2 == 1`。两个竞争者随后争夺
  immutable run output，其中一个报 `BTAG-RUN-PERSIST`，证明事后持久化冲突不能阻止重复执行。

### 4.2 修复后功能验收

| ID | 真实结果 |
| --- | --- |
| A1 | `actions/run-<key-digest>.lock` 覆盖 `run()` 的完整执行路径。第一 worker child-start 后，第二同 key worker 在 release 前未进入 child boundary；最终 child-start 精确为 1，两个 worker success 且 `result_hash` 相同，session 为 `COMPLETED`。 |
| A2 | completed action 只重放已验证的 result，child-start 仍为 1；相同 key + 不同 run token request 返回 `BTAG-IDEMPOTENCY-CONFLICT`，无额外 child。 |
| A3 | 不同 run key 的真实 spawn holder 与本地第二 key lock 可独立进入；两个稳定 `.lock` 文件保留。open、prepare、acquire、release、close 错误均为 `BTAG-RUN-ACTION-LOCK`，descriptor close 被记录。 |
| A4 | `tests/test_run_concurrency.py tests/test_run_resume.py`：9 passed；保留 partial report + paused session 的同 effect resume。 |

### 4.3 发行验收

| ID | 实际结果 |
| --- | --- |
| B1 | base：`pytest tests`，109 passed（既有 Quandl deprecation warning）。 |
| B2 | py38、py312：各 `pytest tests`，109 passed。 |
| B3 | `ruff check src/backtrader_agent tests scripts`：All checks passed。 |
| B4 | `audit_independence.py`：6/6 passed，product root hash `86b3c2cf21f35a36d055cb73beb9c32bb6e7f57cbca147322d88256ad627d397`；`doctor.py`：`status=ready`（未注册 engine root 的 execution-ready false 为预期诊断）。 |
| B5 | `run_acceptance.py`：退出码 0、`status=passed`；wheel build/clean install passed，14 个 acceptance cell 通过（14 passed，1 个既有 Quandl warning，154.21s），crash-resume 与 repair gate 均 passed。 |
| B6 | `build_manifest.py` 生成 root 123 files、package 43 files；随后 distribution contract 与独立性审计通过。 |

结论：A1–A4、B1–B6 均通过。本轮完成；下一轮继续处理第 006 轮明确递延的 immutable record 同内容
竞争重读，使安全的 `BTAG-WRITE-EXISTS` 不泄漏为正常重试 caller 的失败。
