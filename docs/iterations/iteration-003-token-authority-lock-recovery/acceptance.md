# 迭代 003：TokenAuthority 并发锁与恢复验收文档

## 1. 功能门

| ID | 验收项 | 通过条件 |
| --- | --- | --- |
| A1 | secret bootstrap 并发 | 8 个 spawn worker 全部成功、返回同一 32-byte secret；无 `BTAG-WRITE-EXISTS` |
| A2 | 已有 secret 兼容 | 合法既有 bytes 不变化；非法长度仍为 `BTAG-TOKEN-SECRET` |
| A3 | approval 遗留锁恢复 | 预先创建的旧 `.lock` 不阻塞进入临界区；路径在 release 后保留 |
| A4 | approval 正常竞争 | 两个真实进程同 ID 都可在有限时间内完成临界区，不产生 `BTAG-APPROVAL-BUSY` |
| A5 | request 隔离与 OS 错误 | 不同 ID 路径不同且能同时进入；open/acquire/release 异常为稳定 lock code，descriptor 已关闭 |
| A6 | SessionStore 兼容 | 迭代 002 的 session 多进程、recover 和 lock-error 用例仍通过 |

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

A1–A6 与 B1–B6 均通过，验收记录包含红测现象、真实进程数、稳定错误 code 和最终
清单结果，才可进入下一轮审计。

## 4. 实际验收记录（2026-08-02）

### 4.1 红测与功能门

| 项 | 红测/旧行为 | 修复后结果 |
| --- | --- | --- |
| A1 | 8 个 spawn worker 在同一首次 secret 写入窗口竞争；至少一个返回 `BTAG-WRITE-EXISTS`，而非所有调用者成功。 | `test_secret_bootstrap_is_linearized_across_processes` 通过：8/8 success，返回同一 hex secret，最终 `token-secret.key` 长度为 32 bytes。 |
| A2 | 既有语义基线。 | 合法既有 secret 原样复用；写入 `b"invalid"` 后稳定报告 `BTAG-TOKEN-SECRET`。 |
| A3 | 手工留下 `<request-id>.lock` 后，旧 `_approval_lock()` 立即抛出 `BTAG-APPROVAL-BUSY`。 | 新锁可获取同一遗留路径；退出后 lock file 仍存在，供下次进程安全复用。 |
| A4 | 两个 spawn worker 竞争同一 request 时，一个返回 `BTAG-APPROVAL-BUSY`。 | 两个 worker 均 success；后到者等待 OS lock 后顺序完成，不再把正常竞争当作业务失败。 |
| A5 | 缺少调用边界的 lock error 映射与隔离证明。 | 不同 request ID 同时跨越 barrier，路径不同且均成功；注入 `os.open` 错误得到 `BTAG-APPROVAL-LOCK`。既有 session acquire/release 错误测试继续确认 descriptor close 与 `BTAG-SESSION-LOCK`。 |
| A6 | 共享原语可能改变 session 状态持久化。 | `tests/test_token_concurrency.py` 与 `tests/test_tokens_changes_sessions.py` 联合 19 项通过，包括 create/transition/recover hash-chain 多进程回归。 |

### 4.2 发行门

| 门 | 实际结果 |
| --- | --- |
| B1 | base 完整 `pytest tests`：退出码 0。 |
| B2 | py38、py312 完整 `pytest tests`：分别退出码 0。 |
| B3 | `ruff check src/backtrader_agent tests scripts`：`All checks passed!` |
| B4 | `scripts/audit_independence.py`：6/6 checks passed；`scripts/doctor.py`：`status=ready`、两个 execution profile ready。当前没有注册 engine root，因此 `execution_ready=false` 为预期提示。 |
| B5 | `scripts/run_acceptance.py`：`status=passed`；clean wheel 来源探针通过，14 个 matrix cell、crash-resume 与 repair 全部通过；matrix 为 `14 passed, 1 warning in 146.56s`。 |
| B6 | `scripts/build_manifest.py`：root 100 files、package 43 files；后续 manifest contract 和 independence audit 均通过。 |

唯一警告仍来自 Backtrader 已弃用的 Quandl feed import，不影响离线验收。
