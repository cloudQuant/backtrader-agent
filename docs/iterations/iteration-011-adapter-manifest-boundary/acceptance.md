# 迭代 011：Adapter manifest 与链接完整性验收文档

## 1. 功能门

| ID | 验收项 | 通过条件 |
| --- | --- | --- |
| A1 | path traversal manifest | legacy `../victim` manifest 被拒绝为 `BTAG-UNINSTALL-MANIFEST`；target 外 marker、adapter、manifest 全部保持。 |
| A2 | manifest shape safety | symlink manifest、未知/重复 relative path、非法 hash/schema/host 都零写入拒绝。 |
| A3 | symlink identity | same-byte adapter symlink 的 install preview 返回 `BTAG-INSTALL-CONFLICT`；uninstall 返回 `BTAG-UNINSTALL-MODIFIED`，不 unlink。 |
| A4 | lifecycle regression | install/uninstall preview、hash conflict、same-host spawn uninstall、different-host lock isolation 继续通过。 |

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

必须先保留 A1 的旧实现外部 marker 删除 red 证据。A1–A4、B1–B6 全部通过，且拒绝分支均证实 zero-write，
才可完成本轮；实际结果将在开发/验收后填入。

## 4. 实际验收记录

### 4.1 旧实现红测

在已安装 claude adapter 的临时 target 中，将 installer manifest 篡改为携带 `../victim.txt` 和 target 外
marker 的真实 hash。旧 `uninstall(..., apply=True)` 没有抛出错误，并会删除该 marker 和 manifest。另三个
red test 证明旧实现也会跟随 manifest symlink、把同内容 adapter symlink 在 preview 中报为 `unchanged` 并
删除，且接受 wrong schema/host、缺项、重复项、未知 path 或非法 hash 的 manifest。

### 4.2 修复后功能验收

| ID | 实际结果 |
| --- | --- |
| A1 | `../victim.txt` 在 allowlist 解析阶段返回 `BTAG-UNINSTALL-MANIFEST`；target 外 marker、adapter、manifest 均保留。 |
| A2 | manifest symlink、wrong schema/host、空/重复/未知 entries、非法 hash 均返回 `BTAG-UNINSTALL-MANIFEST`，拒绝分支零写入。 |
| A3 | same-byte adapter symlink 的 install preview 返回 `BTAG-INSTALL-CONFLICT`；uninstall 返回 `BTAG-UNINSTALL-MODIFIED`，链接、manifest 和外部目标均保留。 |
| A4 | 11 项 installer concurrency/lifecycle tests（含真实 same-host spawn unlink race、different-host isolation、preview zero-write、lock fault mapping）全部通过。 |

### 4.3 发行验收

| ID | 实际结果 |
| --- | --- |
| B1 | base：完整 `pytest tests`，128 项通过（仅既有 Quandl deprecation warning）。 |
| B2 | py38、py312：各完整 `pytest tests`，128 项通过。 |
| B3 | `ruff check src/backtrader_agent tests scripts`：All checks passed。 |
| B4 | `audit_independence.py`：6/6 passed，product root hash `f49ee8ed1e0ef0cacb638ec352fee79ab6557cf268fb5518c6bd19fd95f98d8d`；`doctor.py`：`status=ready`，未注册 engine root 的 execution-ready false 为预期诊断。 |
| B5 | `run_acceptance.py`：退出码 0、`status=passed`；wheel build/clean install passed，14 个 matrix cell 通过（14 passed，1 个既有 Quandl warning，143.68s），crash-resume 与 repair gate 均 passed。 |
| B6 | `build_manifest.py` 生成 root 145 files、package 43 files；随后 distribution contract 与独立性审计通过。 |

结论：A1–A4、B1–B6 全部通过。本轮完成；进入最终收敛审计，仅在能证明仍有本地、用户可见且可安全修复的
缺口时继续建立新的迭代。
