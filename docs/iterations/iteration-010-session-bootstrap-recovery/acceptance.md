# 迭代 010：会话创建引导恢复验收文档

## 1. 功能门

| ID | 验收项 | 通过条件 |
| --- | --- | --- |
| A1 | manifest publish crash retry | spawn worker 在实际 manifest publish 边界退出；重试 `create` 返回合法 NEW manifest、空 journal 与有效 checkpoint。 |
| A2 | safe bootstrap classification | journal 不存在时正常创建；无 manifest 的空普通 journal 可复用。 |
| A3 | conservative refusal | 非空或 symlink journal 返回 `BTAG-SESSION-BOOTSTRAP`，原内容/对象不变。 |
| A4 | concurrency regression | 既有 same-session create、transition、recover/transition 与不同 session 互斥测试通过。 |

## 2. 发行门

| ID | 命令 | 通过条件 |
| --- | --- | --- |
| B1 | base `pytest tests` | 全部通过。 |
| B2 | py38、py312 `pytest tests` | 全部通过。 |
| B3 | `ruff check src/backtrader_agent tests scripts` | 通过。 |
| B4 | `scripts/audit_independence.py`、`scripts/doctor.py` | audit passed；doctor 输出合法。 |
| B5 | `scripts/run_acceptance.py` | clean wheel、14-cell matrix、crash-resume、repair 均通过。 |
| B6 | `scripts/build_manifest.py` 后 distribution contract audit | manifests 新鲜且不含本机缓存。 |

## 3. 完成判定

先保留 A1 在旧实现上的 red 证据；仅在 A1–A4、B1–B6 全部通过，且拒绝路径无 silent repair 后才可标记
本轮完成。实际结果将在开发和验收后填入本文件。

## 4. 实际验收记录

### 4.1 旧实现红测

真实 `spawn` worker 在 `manifest.json` 的 `atomic_write_json(..., create_only=True)` 调用点执行
`os._exit(79)`，此时空 `journal.jsonl` 已 fsync、manifest 尚不存在。旧实现的 retry 在 journal
create-only 路径报 `BTAG-WRITE-EXISTS`。对无 manifest 的非空普通 journal 与指向空文件的 symlink
journal，旧实现也只返回同一个泛化诊断，而非表明 bootstrap 状态不安全。

### 4.2 修复后功能验收

| ID | 实际结果 |
| --- | --- |
| A1 | worker 退出码为 79；重试 `create` 返回合法 NEW manifest，`last_sequence=0`、tail hash 为 64 个 `0`，journal 仍为空，`load` 的 checkpoint 校验通过。 |
| A2 | manifest 缺失且 journal 不存在仍正常创建；manifest 缺失且 journal 为普通空文件则安全复用，仅发布 manifest。 |
| A3 | 非空 journal 和 symlink journal 均返回 `BTAG-SESSION-BOOTSTRAP`，原字节/链接和无 manifest 状态保持不变。 |
| A4 | `test_tokens_changes_sessions.py`、`test_persistence_concurrency.py`、distribution 与 runner/installer 集成回归共同通过；同 session create、transition、recover 路径未回归。 |

### 4.3 发行验收

| ID | 实际结果 |
| --- | --- |
| B1 | base：完整 `pytest tests`，124 项通过（仅既有 Quandl deprecation warning）。 |
| B2 | py38、py312：各完整 `pytest tests`，124 项通过。 |
| B3 | `ruff check src/backtrader_agent tests scripts`：All checks passed。 |
| B4 | `audit_independence.py`：6/6 passed，product root hash `67735e42ef55240574364f69ae012726d5d13ae675857a18936df42fc155e838`；`doctor.py`：`status=ready`，未注册 engine root 的 execution-ready false 为预期诊断。 |
| B5 | `run_acceptance.py`：退出码 0、`status=passed`；wheel build/clean install passed，14 个 matrix cell 通过（14 passed，1 个既有 Quandl warning，151.90s），crash-resume 与 repair gate 均 passed。 |
| B6 | `build_manifest.py` 生成 root 140 files、package 43 files；随后 distribution contract 和独立性审计通过。 |

结论：A1–A4、B1–B6 均通过。本轮完成；下一步继续审计 external adapter 的 manifest/链接完整性，避免把
用户替换的 symlink 或伪造 manifest 当作本产品创建的可删除文件。
