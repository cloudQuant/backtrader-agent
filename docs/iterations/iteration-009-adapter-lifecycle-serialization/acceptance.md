# 迭代 009：Adapter 生命周期串行化验收文档

## 1. 功能门

| ID | 验收项 | 通过条件 |
| --- | --- | --- |
| A1 | same-host uninstall race | 真实 spawn unlink-boundary race：修复后一个 `uninstalled`、一个 `BTAG-UNINSTALL-MANIFEST`，无 raw filesystem error，adapter/manifest 删除完整。 |
| A2 | apply lifecycle scope | install/uninstall apply 共享 path；different host lock 能并行；锁文件保留。 |
| A3 | lock diagnostics | open/acquire/release/close 均为 `BTAG-INSTALL-LOCK`，descriptor 关闭。 |
| A4 | preview/contract regression | preview zero-write，install repeat、hash-conflict refuse、uninstall preview/apply 保持原契约。 |

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

必须保留 A1 的旧 raw unlink race 证据。A1–A4、B1–B6 均通过且记录最终 target 状态后，才可标记本轮完成。

## 4. 实际验收记录

### 4.1 旧实现红测

初始 claude adapter 已安装。两个真实 `spawn` uninstall worker 在第一个实际 adapter file
`Path.unlink()` 边界汇合。旧结果为：一个 `("success", "uninstalled")`，另一个
`("unexpected-error", "FileNotFoundError")`。这证明 manifest/removal plan 的 check-then-unlink
不是跨进程线性化操作。

### 4.2 修复后功能验收

| ID | 真实结果 |
| --- | --- |
| A1 | 两个同 host spawn uninstall worker 使用同一 lock；结果恰为一个 `uninstalled`、一个 `BTAG-UNINSTALL-MANIFEST`，无 raw filesystem error。adapter file 与 manifest 已删除，稳定 `.lock` 保留。 |
| A2 | claude holder lock 存在时，codex lock 可立即进入；两个 host 的 lock files 均存在，证明按 host 隔离。 |
| A3 | prepare、acquire、release、close 四种 shared-lock 注入失败均映射 `BTAG-INSTALL-LOCK`。 |
| A4 | 空 target 的 install preview 不创建 `.claude` 或 `.backtrader-agent`；uninstall preview 缺 manifest 亦无写入。既有 install repeat、resource consistency、hash-safe uninstall 回归通过。 |

### 4.3 发行验收

| ID | 实际结果 |
| --- | --- |
| B1 | base：`pytest tests`，121 passed（既有 Quandl deprecation warning）。 |
| B2 | py38、py312：各 `pytest tests`，121 passed。 |
| B3 | `ruff check src/backtrader_agent tests scripts`：All checks passed。 |
| B4 | `audit_independence.py`：6/6 passed，product root hash `34ae89c82fe46eedb559862cc715cfce58b6d1c98a1015e825aa748b59696dfd`；`doctor.py`：`status=ready`（未注册 engine root 的 execution-ready false 为预期诊断）。 |
| B5 | `run_acceptance.py`：退出码 0、`status=passed`；wheel build/clean install passed，14 个 acceptance cell 通过（14 passed，1 个既有 Quandl warning，152.75s），crash-resume 与 repair gate 均 passed。 |
| B6 | `build_manifest.py` 生成 root 135 files、package 43 files；随后 distribution contract 与独立性审计通过。 |

结论：A1–A4、B1–B6 均通过。本轮完成；已收敛目前从 static search、真实并发 red tests、发行矩阵和
独立性审计中能证明的用户可见竞争/恢复缺口，下一步进入最终收敛审计并把环境/产品边界与待外部验证项
明确列出，而非继续扩大范围。
