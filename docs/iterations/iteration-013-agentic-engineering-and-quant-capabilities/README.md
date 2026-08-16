# 迭代 013：Agentic 工程化与量化能力扩展

第 012 轮最终收敛审计在"当前产品承诺内"停止增加迭代。本轮以行业最佳实践(eval-first、
agent-harness-construction、enterprise-agent-ops)重新审视产品,确认在**产品承诺本身**与
**Agent 工程化**两个维度上存在实质差距:宿主 LLM 可用性从未被度量、工具面对 LLM 调用者不友好、
宿主侧可观测性为零、策略优化环缺失。本轮分两轨并行推进:Agentic 工程化线与量化功能线。

| 文档 | 用途 |
| --- | --- |
| [需求文档](requirements.md) | 两轨四阶段的功能与非功能需求 |
| [设计文档](design.md) | envelope 契约、eval harness、sweep 安全模型等设计决策 |
| [验收文档](acceptance.md) | 每阶段验收门与发行门 |
| [实施计划](implementation-plan.md) | 测试先行的分阶段实施路径 |

## 编排

```
Phase 0 地基(工程轨先行)
  └ 工具面契约 + 工程健康(缓存/注册表单源/大文件拆分/死代码清理)
Phase 1 双轨并行
  ├ 工程轨: Eval harness + payload 重写 + 提示词版本化
  └ 功能轨: 瞬态失败重试 + 参数 sweep/优化环 v1
Phase 2 双轨并行
  ├ 工程轨: 宿主追踪 + stderr 保留 + doctor 状态审计 + 跨会话记忆
  └ 功能轨: 分析器扩展 + Sizers
Phase 3 收尾
  └ 指标注册表 + Timers/cheat 模式 + 全量回归
```

每阶段独立验收、独立可发布;Phase 1 结束即可停止并获得完整价值。

## 边界

- **审批模型不变弱**。sweep 引入新的 token 种类,但一次审批只覆盖确定性的枚举计划;不引入
  "任意代码执行"或跳过校验的路径。
- **产品政策不变**。继续离线优先、不嵌入 model SDK、不要求 API key(opt-in LLM 评测门除外,且
  不阻塞 CI);不引入实盘交易、在线数据、OS 级沙箱声明。
- **不引入新的强依赖**。sweep 用标准库实现,不用 optuna/遗传算法;记忆存储为轻量 JSON 文件,
  不引入数据库。
- **诚实边界保留**。`entry`/`exit`/`risk` 字段继续按文档明示不翻译成逻辑;`sizing` 字段本轮
  有限度落地(fixed/percent 两种方法),其余留待后续迭代。
- entry-points 动态插件机制不在本轮范围;注册表先收敛为单源事实,插件化留待下一轮。

## 验收结论

**2026-08-16:全部阶段完成,发行门通过。** 详见[验收文档](acceptance.md),最终证据摘要:

- `python -m pytest tests -q -p no:cacheprovider`:**385 passed**(仅既有 Backtrader
  Quandl 弃用警告与宿主环境 engine 来源提示)。
- `python scripts/run_evals.py`:**23/23**(`{"failed": 0, "passed": 23, "total": 23}`)。
- `python scripts/run_acceptance.py`:clean-wheel 矩阵 pytest **14 passed(130.17s)**,
  14 cell 全部 passed(comparison 全部 passed);独立 gate `crash_resume`/`repair`/
  `sweep` 全部 passed。总状态 `failed` 仅因 `skills_absent=false` —— 宿主 anaconda
  site-packages 含既有 `backtrader-skills` 包(环境性、先于本迭代存在;CI 干净 venv
  不受影响;`mcp_absent=true`)。`matrix.passed=false` 是同一根因的连带,矩阵本身
  14/14 全绿。
- `python scripts/audit_independence.py`:6/6 passed;`python scripts/doctor.py`:
  status=ready;`ruff check src tests scripts`:All checks passed;
  `python scripts/build_manifest.py`:重生成后零 diff。
- 安全 red tests(伪造/重放/越界/越权)全绿,审批模型无弱化证据。
- 文档同步完成:README(EN+CN)、CHANGELOG 0.2.0(含 spec-hash 兼容性说明)、
  `docs/evals/payload-changelog.md` 13.0.3 基线(23/23)、
  `docs/iterations/final-convergence-audit.md` 增补(停止决定被本轮取代)、manifest 重生成。
