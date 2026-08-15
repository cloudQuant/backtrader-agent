# 迭代 013：实施计划

> 原则:测试先行(RED → GREEN → IMPROVE);每阶段以验收文档的对应门为准;每阶段结束可独立
> 发布。阶段内的"工程轨/功能轨"任务互不共享文件时可并行推进。

## Phase 0:工具面契约与工程健康(预计 2 周)

1. **R1/R2 envelope 与退出码(RED 先行)**
   - RED:`tests/test_cli_contract.py` 断言全部子命令成功输出 `{"status": "ok", ...}`、
     失败输出 `{"status": "failed", ...}`、exit code 0/2/3/4 区分。
   - GREEN:改 `_emit()`/`main()` 异常分层;迁移既有 17 个测试文件中的输出断言。
   - IMPROVE:`doctor --json` 实际生效(R8 顺带),warning 走 stderr 且不破坏 stdout JSON。
2. **R4 内联 JSON 输入**
   - RED:同一 `--spec` 分别传内联 JSON、`@file`、文件路径,断言等价。
   - GREEN:统一 `_load_json_arg()` 解析顺序。
3. **R3 action schema**
   - RED:`actions --json` 输出可被 `actions-v1.schema.json` 校验,且与打包资源
     `resources/actions-v1.json` 逐字节一致;每个 leaf 参数含 type/required。
   - GREEN:argparse 反射器 + 打包脚本更新(`scripts/build_manifest.py` 同步收录新资源)。
4. **R6 注册表单源**
   - RED:一致性测试——7 archetype/6 adapter 在 `contracts`/`scaffold`/`catalog`/`data`
     的枚举均可从注册表派生;`canonical_csv_v1` 不再出现在 allowlist。
   - GREEN:新建 `archetypes.py`/`adapters.py`,替换三处硬编码;删除不一致值。
5. **R7 缓存**
   - RED:`tests/test_cache_semantics.py`——同一进程内两次 engine 哈希/feed 哈希只计算
     一次(计数器);跨进程无持久缓存文件;catalog 加载只做一次 manifest 级哈希。
   - GREEN:进程内 memoize;`CatalogSnapshot` 验证改 manifest 级;catalog 热路径基准。
6. **R8 拆分与死代码**
   - RED:拆分后全量回归不降绿;`_single_test_source` 模板函数 golden 输出测试。
   - GREEN:`runner/`、`changes/` 子模块;`REQUIRED_BINDINGS` 单一定义;`catalog refresh`
     快照接入 `search`/`inspect --snapshot-path`;清理 `build/lib/`(加入 .gitignore 或
     显式删除说明)。
7. **阶段门**:Phase 0 验收门(见 acceptance.md)+ 全部既有发行门。

## Phase 1:双轨并行(预计 3 周)

### 工程轨

1. **R12 payload 重写 + R13 版本化**
   - RED:payload 内容 hash golden 常量;语义测试(菜单行都指向真实子命令;worked trace
     的每条命令在当前 CLI 可执行)。
   - GREEN:重写 `agent-payload.md`(worked trace、BTAG 恢复表、压缩规则)+ `version`
     字段;建立 `docs/evals/payload-changelog.md`。
2. **R9/R10 确定性 harness**
   - RED:先写 5 个骨架任务(1 个完整管线 + 1 个失败注入),断言 harness 能驱动 CLI
     完成/恢复。
   - GREEN:`tests/evals/harness.py` 引擎;任务扩展至 15–25 个(7 archetype 全管线、
     6 adapter 注册、幂等重放、≥4 失败注入);`scripts/run_evals.py`。
   - IMPROVE:harness 纳入 `.github/workflows/ci.yml` 新 job。
3. **R11 opt-in LLM 门**
   - GREEN:`scripts/eval_llm_loop.py`(读取 `BACKTRADER_AGENT_EVAL_API_KEY`,缺失即
     skip),pass@1/pass@3 报告落 `docs/evals/`;文档说明其独立性(不进 runtime)。

### 功能轨

4. **R14 瞬态重试**
   - RED:`tests/test_run_retry.py`——瞬态失败同 effect 重试走通;非瞬态/effect 变化/
     终态会话拒绝(red tests)。
   - GREEN:白名单 + `FAILED → RUN_APPROVED` 迁移 + `retry_of` 字段。
5. **R15–R18 sweep**
   - RED:`tests/test_sweep.py`——参数网格枚举与 cell hash 确定性;越界拒绝;伪造
     SweepPlan 拒绝;token 重放/跨会话拒绝(red tests);`--max-cells` 截断。
   - GREEN:`sweep prepare/run/report` 命令、SweepPlan 记录、`approval --kind sweep`、
     `sweep-result-v1`、cell 级重试;renderer 参数网格注入。
   - IMPROVE:14-cell 风格 sweep 冒烟入 `run_acceptance.py`(小网格 2×2)。
6. **阶段门**:Phase 1 验收门;发行门全绿(含新 eval job)。

## Phase 2:双轨并行(预计 3 周)

### 工程轨

1. **R19 宿主追踪 + R20 stderr 保留**
   - RED:`tests/test_observability.py`——每次 dispatch 产生 trace 行、失败调用也有;
     run 目录存在 `stderr.log`;trace 不含 secret。
   - GREEN:dispatch 钩子 + runner 输出保留。
2. **R21 doctor 审计 + R22 记忆**
   - RED:构造损坏 journal/RUNNING 孤儿/坏 CAS,`doctor --audit` 逐项报出;记忆存储
     原子写与 schema 校验;`data list` 复用路径测试。
   - GREEN:`doctor --audit` 实现;`memory/` 存储;payload 复用指令。

### 功能轨

3. **R23 扩展指标 + R24 Sizers**
   - RED:schema 测试(`extended_metrics` 可选、11 标量 required 不变);sizer 渲染 golden
     测试;validator 白名单 red tests(未白名单 sizer 调用拒绝)。
   - GREEN:runner 模板 analyzers 注册 + scaffold sizing 段 + validator 扩展;真实 cell
     运行验证指标存在与 sizer 生效。

4. **阶段门**:Phase 2 验收门;发行门全绿。

## Phase 3:收尾(预计 2 周)

1. **R25 指标注册表**
   - RED:资产 golden 测试(计数、schema、`source_available=false`);
     `catalog search --kind indicator` 检索测试。
   - GREEN:`scripts/extract_indicator_registry.py`(只读 fork)+ 打包资源 + 搜索支持。
2. **R26 Timers/cheat**
   - RED:spec 校验(默认关、非法块拒绝);validator 白名单 red tests;渲染 golden 测试。
   - GREEN:spec 区块 + validator + 模板渲染段。
3. **收尾**:更新 README/CHANGELOG/manifest;全量回归三解释器 + clean-wheel;
   回填验收文档证据;迭代 012 收敛审计的停止条件声明更新(产品范围已扩展)。

## 依赖与并行性

```text
Phase 0 全部 -> Phase 1 两条轨
R12/R13 (payload) 与 R9/R10 (harness) 有顺序依赖(harness 以 payload 为 spec)
R14 -> R17(重试语义被 sweep cell 复用)
R19 依赖 R1(envelope 稳定后 trace 才有稳定 exit code 语义)
R23/R24 依赖 R6(注册表单源后模板才可扩展)
```

## 测试与验证纪律

- 每个任务先写 RED 测试并确认失败,再实现;提交信息按 `feat/fix/test/docs` 惯例。
- 任何安全模型改动必须附 red test(伪造/重放/越界路径)。
- 每阶段结束跑全套:三解释器 pytest、ruff、black、`audit_independence.py`、`doctor`、
  `run_acceptance.py`、分发契约测试;Phase 1 起加 `scripts/run_evals.py`。
