# 迭代 008：Immutable Record 并发重放验收文档

## 1. 功能门

| ID | 验收项 | 通过条件 |
| --- | --- | --- |
| A1 | exact helper | create 后返回 created；同 bytes race/replay 返回 unchanged；mismatch、symlink、目录为 caller conflict code。 |
| A2 | dataset replay | 两个 spawn same-spec register 都成功；CAS/manifest 精确且不同内容冲突保持。 |
| A3 | artifact + bound-record replay | 两个 spawn same artifact/render 与 same bound record 均成功，签名/哈希一致；不同内容不被接受。 |
| A4 | installer replay | 两个 spawn same host/target apply 都成功，一个 installed、一个 unchanged，文件/manifest 精确；preview/uninstall 回归。 |
| A5 | 既有调用者回归 | data/scaffold/token/installer/run/session 相关测试通过。 |

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

必须保留四类调用者在旧实现下的真实 spawn `BTAG-WRITE-EXISTS` 红测证据。A1–A5、B1–B6 均通过且
记录最终对象/状态后，才可标记本轮完成。

## 4. 实际验收记录

### 4.1 旧实现红测

所有 worker 都在真实 `canonical.os.link(temporary, destination)` no-replace publish 边界汇合。旧实现
四组结果均为一个 success 与一个安全拒绝，且拒绝 code 都是 `BTAG-WRITE-EXISTS`：

| 调用者 | 旧竞争实际结果 |
| --- | --- |
| DatasetService.register | 一个 dataset manifest success、另一个 `BTAG-WRITE-EXISTS`。 |
| ArtifactRenderer.render | 一个 artifact/provenance success、另一个 `BTAG-WRITE-EXISTS`。 |
| TokenAuthority.store_bound_record | 一个 signed record success、另一个 `BTAG-WRITE-EXISTS`。 |
| AdapterInstaller.install | 一个 `installed`、另一个 `BTAG-WRITE-EXISTS`。 |

### 4.2 修复后功能验收

| ID | 真实结果 |
| --- | --- |
| A1 | `create_or_verify_bytes/json` 的首个 caller 返回 `created=True`；相同 bytes/json 返回 `False`；不同 bytes、symlink 返回调用方的 conflict code。 |
| A2 | 两个 spawn same-spec DatasetService worker 都 success，dataset ID/manifest hash 相同，CAS regular file 完整；随后顺序 register 仍返回相同 manifest。 |
| A3 | 两个 spawn ArtifactRenderer worker 的 artifact/record hash 相同，product provenance signature 可重新验证；两个 bound-record worker 的 record hash 相同且可加载验证。 |
| A4 | 两个 claude installer worker 都 success，状态恰为一个 `installed`、一个 `unchanged`，adapter file 与 install manifest 完整，uninstall preview 回归通过。 |
| A5 | `test_immutable_record_concurrency` + data/scaffold/token/installer/distribution focused suite：66 passed（1 个既有 Quandl warning）。 |

### 4.3 发行验收

| ID | 实际结果 |
| --- | --- |
| B1 | base：`pytest tests`，114 passed（既有 Quandl deprecation warning）。 |
| B2 | py38、py312：各 `pytest tests`，114 passed。 |
| B3 | `ruff check src/backtrader_agent tests scripts`：All checks passed。 |
| B4 | `audit_independence.py`：6/6 passed，product root hash `0dbbd4fd991f6106eb2f26656a4a5ebd30abf18c98f0ab9e2b6f86f31532b045`；`doctor.py`：`status=ready`（未注册 engine root 的 execution-ready false 为预期诊断）。 |
| B5 | `run_acceptance.py`：退出码 0、`status=passed`；wheel build/clean install passed，14 个 acceptance cell 通过（14 passed，1 个既有 Quandl warning，172.46s），crash-resume 与 repair gate 均 passed。 |
| B6 | `build_manifest.py` 生成 root 129 files、package 43 files；随后 distribution contract 与独立性审计通过。 |

结论：A1–A5、B1–B6 均通过。本轮完成；下一轮应从剩余 create-only 用法和 lifecycle edge cases 中
筛选是否还有可证实的用户可见竞争/恢复缺口，而不是扩大 immutable replay 到未声明的 mutable API。
