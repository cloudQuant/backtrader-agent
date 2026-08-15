# 迭代 001：可信执行与发行契约验收文档

## 1. 验收原则

本验收先用负向用例证明旧逃逸路径被关闭，再以 root-bound 的完整 14-cell 路径证明正常能力没有被破坏。每一项必须有可重跑命令、明确预期和关联需求；单一绿色 smoke 不得替代全流程证明。

所有 Python 命令使用 Anaconda：`/Users/yunjinqi/opt/anaconda3/bin/conda run -n <environment> python ...`。运行过程中设置 `PYTHONDONTWRITEBYTECODE=1` 并禁用 pytest cache，避免把编译产物写入仓库。

## 2. 功能与安全门

| ID | 验收项 | 命令或测试 | 通过条件 |
| --- | --- | --- | --- |
| A1 | raw 引擎/环境哈希 CLI 路径被移除 | `pytest tests/test_cli_workflow.py -q -p no:cacheprovider` | `--engine-hash`、`--environment-hash` 解析失败；相同 workflow 用 registered engine root 通过 |
| A2 | validation token 有真实 root binding | `test_cli_data_to_run_workflow_is_hash_chained_and_completes` | token 含 `engine_root_id`，其两个 hash 分别等于当前 descriptors 的 hash，不能由传参伪造 |
| A3 | 引擎树篡改拒绝运行 | 新增 `test_run_rejects_engine_tree_mutation_before_token_consumption` | 修改 root 下受纳入文件后得到 `BTAG-ENGINE-HASH`；run token 仍未消费；无 run result |
| A4 | 引擎符号链接与非正常布局拒绝 | 新增 `test_inspect_engine_rejects_symlinked_package_member` | 得到稳定 `BTAG-ENGINE-*` 诊断，不产生 descriptor |
| A5 | child import 仍根绑定 | 现有 `test_controlled_end_to_end_run_and_report` 加断言 | RunManifest 的 root ID、tree hash、import relative path 与 descriptor 一致 |
| A6 | 环境改变在消费前拒绝 | `test_run_rejects_environment_change_before_token_consumption` | `BTAG-ENVIRONMENT-HASH`；没有子进程、token 未消费 |
| A7 | doctor 清楚区分状态 | 新增 doctor 断言 | 无 root 时 `status=ready` 且 `execution_ready=false`；有效 root 与依赖存在时 `execution_ready=true` |

## 3. 发行物与依赖门

| ID | 验收项 | 命令或测试 | 通过条件 |
| --- | --- | --- | --- |
| B1 | wheel metadata 包含 extras | `pytest tests/test_distribution_contracts.py -q -p no:cacheprovider` | wheel `METADATA` 含 `backtest`、`single-test`、`test` 与指定 Requires-Dist |
| B2 | 基础包保持零强依赖 | metadata consumer 断言 | 不含无 marker 的 Backtrader、Pandas、pytest、jsonschema runtime Requires-Dist；基础 `doctor/payload/session create` 可用 |
| B3 | 缺依赖安全失败 | 新增 runner preflight 测试 | profile 缺依赖返回 `BTAG-RUN-DEPENDENCY`，无 child、无 token 消费、无 run artifacts |
| B4 | extras 支持真实运行 | 3.8 与 3.12 clean consumer fixture | `[backtest]` 可执行 python_bundle；`[test]` 可执行 single_test 与 test suite |
| B5 | CI 不再手工拼依赖 | 读取 `.github/workflows/ci.yml` 的结构测试或审查 | CI 使用 `.[test]`；不存在单独的 `pip install backtrader pandas jsonschema pytest` 列表 |

## 4. 兼容与回归门

| ID | 环境 | 命令 | 通过条件 |
| --- | --- | --- | --- |
| C1 | Python 3.8 | `PYTHONDONTWRITEBYTECODE=1 conda run -n py38 python -m pytest tests -q -p no:cacheprovider` | 全部通过；无新增 warning-as-error 或兼容回归 |
| C2 | Python 3.12 | `PYTHONDONTWRITEBYTECODE=1 conda run -n py312 python -m pytest tests -q -p no:cacheprovider` | 全部通过 |
| C3 | 当前开发环境 | `conda run -n base python scripts/audit_independence.py` | status 为 passed，未引入 sibling/host/dynamic-execution 依赖 |
| C4 | 当前开发环境 | `conda run -n base python scripts/doctor.py` | 输出合法 JSON，readiness 字段和 hint 语义正确 |
| C5 | 当前开发环境 | `conda run -n base python scripts/run_acceptance.py` | 7 archetypes × 2 profiles × runonce/runnext、repair、crash-resume、clean wheel isolation 全部通过 |
| C6 | manifests | `conda run -n base python scripts/build_manifest.py` 后执行 distribution-contract test 与独立性审计 | 两个 manifests 与最终工作树精确一致 |

## 5. 文档门

1. `examples/README.md` 的 walkthrough 在任何 `session-001` 引用前调用 `session create`。
2. README 英文和中文、CONTRIBUTING 都给出基础安装与 `[backtest]` 安装的区别，并说明可执行 validation 必须使用 registered read-only engine root。
3. 本目录四份文档互相链接且不包含未定义的接口、无范围外承诺。
4. `LICENSE`、`pyproject.toml` 与 wheel metadata 一致声明 MIT，且没有无关的许可证文本或版本变更。

## 6. 完成判定

技术验收通过需要 A1–A7、B1–B5、C1–C6 和全部文档门均通过，且 diff 仅包含本迭代需求允许的文件。技术通过后立即进行下一轮并发会话锁的需求、设计、验收、实现闭环。

维护者已指定 MIT，因此本轮验收涵盖许可证元数据一致性；通过全部门后不再存在该项发布许可证阻塞。

## 7. 实际验收记录（2026-08-02）

本记录在执行期间设置 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`，避免宿主机器中无关 pytest 插件干扰项目测试；这不改变产品运行时行为。

| 门 | 实际证据 | 结果 |
| --- | --- | --- |
| A1–A7 | 新增 CLI、引擎包树、符号链接、环境变动、依赖预检和 doctor readiness 回归测试；root-bound 单元运行也断言 RunManifest 的 tree hash | 通过 |
| B1–B5 | wheel `METADATA` 断言 MIT、三个 extras 及其依赖；README/CONTRIBUTING/示例/CI 契约测试通过 | 通过 |
| C1 | `py38` 环境先执行 `python -m pip install '.[test]'`，随后完整套件 | 69 passed |
| C2 | `py312` 环境先执行 `python -m pip install '.[test]'`，随后完整套件 | 69 passed |
| C3–C4 | `ruff check`、`scripts/audit_independence.py`、`scripts/doctor.py` | 全部通过；audit 六项检查均为 passed |
| C5 | `scripts/run_acceptance.py` 以临时 clean wheel/site 执行 | `status=passed`；14/14 cells、每 cell 两种 mode；crash-resume 与 repair 均通过；MCP/skills 不可导入 |
| C6 | `scripts/build_manifest.py` 重建根 manifest（286 文件）和包 manifest（42 文件），随后独立性审计通过 distribution manifest 检查 | 通过 |

基础 `doctor` 在没有注册 engine root 时报告 `status=ready`、`execution_ready=false` 并给出注册提示；单元测试同时验证注册只读有效 engine 后 `execution_ready=true`。这是预期的能力分层，不是发布阻塞。

**结论：迭代 001 验收通过。** 下一轮进入跨进程会话锁与并发恢复契约的需求、设计、验收、实现闭环。
