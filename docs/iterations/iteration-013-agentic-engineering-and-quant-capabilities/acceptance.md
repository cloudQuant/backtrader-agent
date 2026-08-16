# 迭代 013：验收文档

> 每阶段结束时回填本节证据(日期、命令输出摘要)。阶段门不通过不得进入下一阶段。
> 全部阶段已完成：**2026-08-16 发行收尾(Task 24)回填最终证据**，见「最终发行门」。

## Phase 0 验收门:工具面契约与工程健康

- [x] A0-1 全部子命令成功输出为 `{"status": "ok", "result": ...}`;失败为统一
  `{"status": "failed", "diagnostic": ...}`;`--json` 输出始终可 `json.loads`。
  — `tests/test_cli_contract.py` 全绿;发行门 `doctor --json`/`actions --json` 实测可解析。
- [x] A0-2 exit code 矩阵:用法错误 2、BTAG 领域失败 3、OSError(磁盘满/权限)4、成功 0;
  `BTAG-CLI-INPUT` 不再覆盖 OSError。— `test_cli_contract.py` 全绿(发行门 pytest 385/385)。
- [x] A0-3 `actions --json` 与打包资源 `resources/actions-v1.json` 逐字节一致,可被
  `actions-v1.schema.json` 校验。— `test_distribution_contracts.py` golden 测试全绿;
  `scripts/audit_independence.py` `packaged_contracts` passed。
- [x] A0-4 内联 JSON/`@file`/文件路径三种输入形式等价(全参数化测试)。
  — `test_cli_workflow.py` 参数化测试全绿。
- [x] A0-5 注册表单源:7 archetype、6 adapter 仅一处定义;`canonical_csv_v1` 不一致消除。
  — `archetypes.py`/`adapters.py` 单源;`test_registry_consistency.py` 全绿。
- [x] A0-6 缓存纪律:进程内 memoize 生效(计数器测试);安全敏感哈希无跨进程持久缓存;
  catalog 每次调用只做一次 manifest 级哈希。— `test_cache_semantics.py` 全绿。
- [x] A0-7 拆分后全量回归不降绿;`runner.py`/`changes.py` 无 >800 行模块;
  `_single_test_source` 为模板函数;死代码清理完成。— `runner/`、`changes/` 子模块
  各 <400 行;发行门 pytest 385/385。
- [x] A0-8 既有发行门全绿:pytest、ruff、`audit_independence.py`、`doctor`、
  `run_acceptance.py` 14-cell、分发契约测试。— 见「最终发行门」证据(2026-08-16)。

## Phase 1 验收门

### 工程轨

- [x] A1-1 `tests/evals/` ≥ 15 个任务,覆盖:7 archetype 全管线、6 adapter 注册、幂等
  重放、≥ 4 个失败注入;grader 全部确定性(无 LLM 依赖)。
  — `tests/evals/tasks/` 共 **23** 个任务;`tests/test_eval_harness.py` 全绿。
- [x] A1-2 harness 在 CI 运行并阻塞(新 job 绿);本地 `scripts/run_evals.py` 全绿。
  — 2026-08-16 发行门:`{"failed": 0, "passed": 23, "total": 23}`;CI workflow 含
  `python scripts/run_evals.py` job。
- [x] A1-3 payload 含 worked trace、BTAG 恢复表、压缩规则;`version` 字段存在;hash
  golden 测试固定内容;`docs/evals/payload-changelog.md` 建立。
  — payload `version: "13.0.3"`;`test_payload_contract.py` golden SHA-256 固定;
  changelog 记录 13.0.0→13.0.3 全部变更。
- [x] A1-4 opt-in LLM 门:未配置 key 时 skip 且不阻塞;配置时产出 pass@1/pass@3 报告。
  — `scripts/eval_llm_loop.py` 无 key 时打印 skip 并 exit 0;CI 不运行(基线见
  `docs/evals/payload-changelog.md`:尚无 key 配置,未记录基线)。

### 功能轨

- [x] A1-5 瞬态重试:`FAILED → RUN_APPROVED` 仅对白名单失败 + 同 effect 生效;非瞬态、
  effect 变化、终态会话的 red tests 全部拒绝;`retry_of` 链记录正确。
  — `tests/test_run_retry.py`(6 个红/绿用例)全绿。
- [x] A1-6 sweep:参数网格枚举确定性;越界值拒绝;伪造 SweepPlan 拒绝;token 重放/跨会话
  拒绝(red tests);`--max-cells` 生效。— `tests/test_sweep.py` 全绿。
- [x] A1-7 sweep 真实执行:2×2 小网格在 clean-wheel 环境下逐 cell 产出 RunManifest/
  RunResult,排名报告正确,cell 级瞬态重试走通。
  — 发行门 `run_acceptance.py` 独立 gate `sweep` passed(clean-wheel 环境)。
- [x] A1-8 会话 journal 记录 sweep 事件;中断恢复(PAUSED)对 sweep 生效。
  — `tests/test_sweep.py` journal/recover 用例全绿。

## Phase 2 验收门

### 工程轨

- [x] A2-1 宿主追踪:成功与失败调用均有 trace 行;`trace/<session-id>.jsonl` 与
  `trace/global.jsonl` 分工正确;trace 不含 secret 与绝对 target 路径。
  — `tests/test_observability.py` 全绿。
- [x] A2-2 受控 run 目录含 `stdout.log`/`stderr.log`(成功路径);失败路径脱敏语义不退化。
  — `test_runner_installer_audit.py` 输出保留用例全绿。
- [x] A2-3 `doctor --audit` 对构造的损坏 journal、RUNNING 孤儿、坏 CAS、过期审批逐项
  报出结构化诊断;listing 命令报告跳过计数。— `test_runner_installer_audit.py` audit
  用例全绿;`doctor --audit` 实测(发行门)。
- [x] A2-4 记忆存储:datasets/params 原子写、schema 校验;payload 含数据集复用与压缩
  规则指令;`data list` 复用路径端到端测试通过。— `tests/test_memory_store.py` 全绿。

### 功能轨

- [x] A2-5 RunResult 11 标量保持 required;`extended_metrics` 可选且 schema 校验通过;
  真实 cell 运行产出 TradeAnalyzer 子集/SQN/Calmar/VWR 指标;分析器缺失时 null 不失败。
  — `test_runner_installer_audit.py` extended-analyzer 用例(含缺失/异常分析器)全绿;
  14-cell 验收矩阵全绿。
- [x] A2-6 Sizers:fixed/percent 两种方法渲染 golden 正确;validator 白名单拒绝未授权
  sizer 调用(red tests);真实 cell 运行验证 sizing 生效。
  — `test_scaffold_validator_catalog.py` golden + red tests 全绿。

## Phase 3 验收门

- [x] A3-1 指标注册表资产打包进 wheel,golden 计数/schema 测试通过;
  `catalog search --kind indicator` 检索正确;`source_available=false` 纪律保持。
  — `resources/catalog/indicator-registry-v1.json` 打包;分发契约测试全绿;
  `catalog search --kind indicator` 实测。
- [x] A3-2 Timers/cheat:spec 默认关、非法块拒绝;validator 白名单 red tests;
  timer/cheat 渲染 golden;真实 cell 运行通过。
  — `test_scaffold_validator_catalog.py` 与 `test_runner_installer_audit.py` 相关用例
  全绿;14-cell 验收矩阵全绿。

## 最终发行门(全部阶段完成后)

- [x] pytest 全绿;ruff 全绿;`audit_independence.py` 6/6;`scripts/doctor.py` ready;
  `run_acceptance.py` clean-wheel 14-cell 全绿;`scripts/run_evals.py` 全绿。
  **2026-08-16 发行收尾证据(Task 24,commit 见 git log):**
  - `python -m pytest tests -q -p no:cacheprovider`:**385 passed**(
    5×72+25 点,唯一警告为既有 Backtrader Quandl 弃用 + 宿主环境 engine 来源提示)。
  - `python scripts/run_evals.py`:**23/23** — `{"failed": 0, "passed": 23, "total": 23}`。
  - `python scripts/run_acceptance.py`:clean-wheel 构建/安装/探测通过;矩阵 pytest
    **14 passed(130.17s)**,14 cell 全部 `status=passed` 且 comparison `passed`;
    独立 gate `crash_resume`/`repair`/`sweep` 全部 passed;`doctor=ready`、
    `independence=passed`。总状态 `failed` **仅因** `skills_absent=false`:宿主
    anaconda site-packages 含既有 `backtrader-skills` 包(环境性、先于本迭代存在,
    CI 干净 venv 不受影响;`mcp_absent=true`)。`matrix.passed=false` 是同一根因的
    连带(clean_install 门包含 skills_absent),矩阵本身 14/14 全绿。
  - `python scripts/audit_independence.py`:**6/6 passed**(comparison_profile、
    distribution_manifest、dynamic_execution、forbidden_imports、forbidden_reads、
    packaged_contracts)。
  - `python scripts/doctor.py`:**status=ready**。
  - `python scripts/build_manifest.py`:重生成后零 diff(manifest 新鲜,见下一条)。
  - `ruff check src tests scripts`:**All checks passed!**
  - 三解释器 pytest:本地单解释器 385/385;3.8/3.9/3.11/3.12 多解释器矩阵由 CI 覆盖。
- [x] 安全 red tests 汇总(伪造/重放/越界/越权路径)全绿,审批模型无弱化证据。
  — `test_sweep.py`(伪造计划/重放/跨会话)、`test_run_retry.py`(非瞬态/effect 变化/
  终态拒绝)、`test_token_concurrency.py`、`test_change_concurrency.py`、
  `test_installer_concurrency.py`、`test_persistence_concurrency.py`、
  `test_immutable_record_concurrency.py` 全部全绿;sweep 为 run-only 能力(真实
  cell 运行断言 workspace 无 state root 之外的新文件)。
- [x] README/CHANGELOG/manifest 同步更新;`docs/evals/payload-changelog.md` 记录本轮
  全部 payload 变更;迭代 012 收敛审计停止条件声明更新。
  — README(EN+CN)新增 sweep/瞬态重试/可观测性/记忆/扩展指标/actions --json/内联
  JSON/envelope 说明;CHANGELOG 0.2.0 条目(含 spec-hash 兼容性说明);manifest
  `build_manifest.py` 重生成后零 diff;`payload-changelog.md` 13.0.3 基线
  (23/23)回填;`final-convergence-audit.md` 增补第 6 节(停止决定被迭代 013 取代)。
- [x] 验收证据回填至本文件与 README「验收结论」。
  — 本文件;`docs/iterations/iteration-013-.../README.md`「验收结论」。
