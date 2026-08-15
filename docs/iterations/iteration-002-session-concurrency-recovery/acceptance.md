# 迭代 002：会话并发与恢复契约验收文档

## 1. 验收原则

必须先观察未加锁实现的真实多进程竞争失败，再验证锁后每个返回成功的 transition 都保留在单一 journal hash chain 中。线程级单元测试或只检查最终 manifest 都不足以替代该证据。

所有 Python 命令使用 Anaconda；测试设置 `PYTHONDONTWRITEBYTECODE=1`、`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` 和 `-p no:cacheprovider`，避免宿主插件与字节码影响结果。

## 2. 功能门

| ID | 验收项 | 证据 | 通过条件 |
| --- | --- | --- | --- |
| A1 | 同 session 竞争 transition | 新增多进程 spawn/barrier test | 一次合法推进；journal sequence 为 `[1]`；checkpoint 与尾 hash 一致；无 corrupt tail |
| A2 | 成功 transition 不丢失 | A1 worker result 与最终 journal 比对 | 每个 success 都有唯一对应 event；不存在重复 sequence/分叉 previous hash |
| A3 | 并发 create 幂等 | 新增多进程 create test | 两者返回同一合法 NEW manifest；只产生一个 journal/manifest |
| A4 | recover 与 transition 互斥 | 新增阻塞/竞争 test | recovery 不截断合法并发 event；最终 state 和 chain 可验证 |
| A5 | 不同 session 不串行化 | 新增 lock-path/worker timing test | 两个不同 session 可独立进展，无共享 lock 文件 |
| A6 | 锁错误可诊断 | 模拟 OS lock 获取/释放错误 | `BTAG-SESSION-LOCK`，无 descriptor 泄漏 |
| A7 | 发布清单排除本机缓存 | 临时 `.mypy_cache`/`.ruff_cache` fixture 与 source manifest 测试 | 缓存不出现在 file map；普通文件仍被覆盖；重新生成的清单不依赖本机缓存 |

## 3. 回归与发行门

| ID | 命令 | 通过条件 |
| --- | --- | --- |
| B1 | base `pytest tests` | 全部通过 |
| B2 | py38、py312 `pytest tests` | 全部通过 |
| B3 | `ruff check src/backtrader_agent tests scripts` | 通过 |
| B4 | `scripts/audit_independence.py`、`scripts/doctor.py` | audit passed；doctor 输出合法 readiness |
| B5 | `scripts/run_acceptance.py` | clean wheel、14 cell、crash-resume、repair 全部通过 |
| B6 | `scripts/build_manifest.py` 后 distribution manifest audit | 两个 manifest 新鲜 |

## 4. 完成判定

A1–A7 与 B1–B6 全部通过，且迭代报告记录真实进程数、结果与最终 chain 证据，方可进入下一轮审计。若仍可构造同 session 重复 sequence、一个成功调用被 recover 丢弃，或质量工具缓存会改变发行清单，则本迭代不得验收。

## 5. 实际验收记录（2026-08-02）

### 5.1 先红后绿的定向证据

| 项 | 红测/风险复现 | 修复后结果 |
| --- | --- | --- |
| A1/A2 | 未加锁实现下，8 个 spawn worker 对同一 NEW session 均可返回 success，代表从同一 checkpoint 派生竞争事件。 | `test_same_session_transitions_are_linearized_across_processes` 通过：8 worker 中恰 1 个 success，其余为 `BTAG-STATE-TRANSITION`；最终 sequence 为 `[1]`，event hash 与 manifest 尾一致，且没有 corrupt tail。 |
| A3 | 未加锁的并发 create 曾产生 AgentError。 | `test_same_session_create_is_idempotent_across_processes` 通过：8 worker 全部返回相同 checkpoint；最终唯一 session 为 NEW、`last_sequence=0`、journal 为空。 |
| A4 | 同一 RUNNING session 的 recover 和 PASSED transition 可交叉覆盖。 | 两个独立 spawn worker 屏障竞争；测试通过并验证仅允许两种线性化结果：PASSED 事件被 recovery 保留，或 recovery 先追加 PAUSED 且普通 transition 明确返回 `BTAG-STATE-TRANSITION`。两种结果均验证连续 hash chain 与 checkpoint 尾。 |
| A5/A6 | 不同 session 若共享锁会在 barrier 超时；OS lock 失败若未映射会泄露原始异常。 | 不同 ID 在各自 lock file 内同时越过 barrier；open/acquire/release 注入错误均返回 `BTAG-SESSION-LOCK`，acquire/release 测试确认 descriptor 被关闭。 |
| A7 | 新增 cache fixture 的红测显示 `.mypy_cache` 和 `.ruff_cache` 被生成器包含；受污染 manifest 达 291 项。 | 生成器、source-manifest 契约与 clean-copy 统一排除这两个目录；fixture 仅保留普通 `docs/retained.md`，最终 root manifest 为 93 项。 |

### 5.2 发行门结果

| 门 | 实际命令/结果 |
| --- | --- |
| B1 | `PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 conda run -n base python -B -m pytest tests -q -p no:cacheprovider`：退出码 0。 |
| B2 | 同一命令在 `py38` 与 `py312` 环境分别退出码 0。 |
| B3 | `conda run -n base python -m ruff check src/backtrader_agent tests scripts`：`All checks passed!` |
| B4 | `scripts/audit_independence.py`：6/6 checks passed；`scripts/doctor.py`：`status=ready`、两个 execution profile ready。`execution_ready=false` 仅因当前 state root 没有注册 engine root，输出提供了注册提示。 |
| B5 | `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 conda run -n base python scripts/run_acceptance.py`：`status=passed`；clean wheel 安装与来源探针通过，14 个 matrix cell 全部通过（runonce/runnext），crash-resume 与 repair 独立 gate 通过；matrix 运行 `14 passed, 1 warning in 147.70s`。 |
| B6 | `conda run -n base python scripts/build_manifest.py`：root 93 files、package 42 files；随后 source manifest contract 与 independence audit 均通过。 |

唯一警告来自 Backtrader 已弃用的 Quandl feed import，不影响本次离线验收结果。
