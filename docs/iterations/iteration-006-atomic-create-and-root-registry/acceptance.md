# 迭代 006：原子 create-only 与 RootRegistry 线性化验收文档

## 1. 功能门

| ID | 验收项 | 通过条件 |
| --- | --- | --- |
| A1 | create-only 真正 no-clobber | 两个真实 spawn worker 在 publish 边界竞争；恰一个 success、另一个 `BTAG-WRITE-EXISTS`，最终目标是完整赢家 payload 且无残留临时文件。 |
| A2 | JSON/upsert 兼容 | JSON create-only 继承 no-clobber；非 create-only replace 仍正确覆盖既有对象。 |
| A3 | RootRegistry 多 ID 线性化 | 两个真实 spawn register 均 success，最终 list 含两个精确 ID；同 ID/同 record 幂等、不同 record conflict。 |
| A4 | registry lock 恢复与诊断 | 稳定 path 保留；open/acquire/release 失败均为 `BTAG-ROOT-LOCK`，descriptor 关闭。 |
| A5 | 调用者回归 | data/scaffold/change/run/session/token 既有持久化用例通过。 |

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

必须保留 A1 与 A3 的旧实现红测证据。A1–A5 与 B1–B6 均通过并记录真实 worker/最终文件状态，
才可标记本轮完成。

## 4. 实际验收记录

### 4.1 旧实现红测

- A1：两个真实 `spawn` worker 分别发布不同 payload，并在旧 `os.replace()` publish 边界同步。
  旧实现两个 worker 都返回 success（断言实际得到 `2 == 1`），后到 worker 静默替换先到内容。
- A3：两个真实 `spawn` worker 分别注册 `left`、`right`，并在旧 registry 写入边界同步。两个
  调用都返回 success，但最终只保留 `left`（断言实际为 `{'left'} == {'left', 'right'}`）。

### 4.2 修复后功能验收

| ID | 真实结果 |
| --- | --- |
| A1 | `os.link(temporary, destination)` 作为 no-replace publish；竞争结果恰一个 success、另一个 `BTAG-WRITE-EXISTS`，最终文件是一个完整赢家 payload，临时文件清理。 |
| A2 | `atomic_write_json(..., create_only=True)` 同样拒绝已有目标；`create_only=False` 仍可 replace。 |
| A3 | `root-registry.lock` 覆盖完整 load/compare/update/write；两个不同 ID 均保留，同 record 幂等、不同 record 为 `BTAG-ROOT-CONFLICT`。 |
| A4 | open、acquire、release 失败均映射 `BTAG-ROOT-LOCK`；异常路径的 descriptor 被关闭，稳定 lock path 保留。 |
| A5 | `tests/test_persistence_concurrency.py tests/test_data_cas.py tests/test_scaffold_validator_catalog.py`：32 passed。 |

### 4.3 发行验收

| ID | 实际结果 |
| --- | --- |
| B1 | base：`pytest tests`，101 passed（既有 Quandl deprecation warning）。 |
| B2 | py38、py312：各 `pytest tests`，101 passed。 |
| B3 | `ruff check src/backtrader_agent tests scripts`：All checks passed。 |
| B4 | `audit_independence.py`：6/6 passed，product root hash `dbad3975f3282b1e02c4c8ee8c8800a8bb8ed97ac6980d800f6e810b4536c820`；`doctor.py`：`status=ready`（未注册 engine root 的 execution-ready false 为预期诊断）。 |
| B5 | `run_acceptance.py`：退出码 0、`status=passed`；wheel build/clean install passed，14 个 acceptance cell 通过（14 passed，1 个既有 Quandl warning，148.31s），crash-resume 与 repair gate 均 passed。 |
| B6 | `build_manifest.py` 生成 root 117 files、package 43 files；随后 distribution contract 与独立性审计通过。 |

结论：A1–A5、B1–B6 均通过。本轮完成；下一轮仅处理本轮刻意递延的“相同 immutable payload
竞争时上层如何重读并返回幂等成功”问题，不放宽 create-only 的 no-clobber 基础语义。
