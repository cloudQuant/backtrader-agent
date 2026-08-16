# 工作流

P0 工作流是两段式的写入/运行管线:没有 change 审批就不写任何文件,没有独立的
run 审批就不执行任何东西。每一步都返回统一 envelope(`{"status": "ok",
"result": ...}`);文件型参数接受内联 JSON、`@file` 引用或普通路径。

## 1. 准备环境

```bash
backtrader-agent doctor --json
backtrader-agent backtrader check
backtrader-agent --state-root $STATE roots register --id workspace --kind workspace --writable --path /path/to/workspace
backtrader-agent --state-root $STATE roots register --id prices --kind dataset --path /path/to/offline-data
backtrader-agent --state-root $STATE roots register --id engine --kind engine --path /path/to/cloudquant-backtrader
backtrader-agent --state-root $STATE session create --session-id session-001
```

## 2. 登记数据

```bash
backtrader-agent --state-root $STATE data inspect --spec data-spec.json
backtrader-agent --state-root $STATE data register --session-id session-001 --spec data-spec.json
```

支持六个离线 adapter:`generic_csv`、`backtrader_csv`、`yahoo_csv`、
`mt5_csv`、`pandas`、`pandas_custom_lines`。登记把规范 CSV 写入内容寻址存储并
输出 `DatasetManifest`。已登记数据集可跨会话复用:

```bash
backtrader-agent --state-root $STATE data list
```

## 3. 校验 StrategySpec

```bash
backtrader-agent --state-root $STATE spec --session-id session-001 --approve --file strategy-spec.json
```

七个 archetype:`single_data_indicator`、`multi_indicator_system`、
`multi_asset_allocation`、`multi_timeframe`、`pairs_spread`、`order_risk`、
`precomputed_ml` —— 每个都可渲染为 `single_test` 或 `python_bundle`。

## 4. 搜索打包快照并渲染草稿

```bash
backtrader-agent catalog search --query "multi timeframe clock" --top-k 3
backtrader-agent --state-root $STATE draft --session-id session-001 \
  --spec strategy-spec.json --dataset-manifest dataset-manifest.json
```

## 5. 校验草稿(仅 AST,绝不导入)

```bash
backtrader-agent --state-root $STATE validate \
  --artifact-manifest artifact-manifest.json --draft-root $DRAFT_ROOT \
  --session-id session-001 --dataset-hash $DATASET_HASH \
  --engine-root-id engine
```

## 6. 准备并审批变更

```bash
backtrader-agent --state-root $STATE changes prepare --session-id session-001 \
  --draft-root $DRAFT_ROOT --files '[{"source": "...", "target": "..."}]' \
  --target-root-id workspace --validation-token $VALIDATION_TOKEN
backtrader-agent --state-root $STATE approval request --kind change \
  --subject-hash $SUBJECT --bindings '{"...": "..."}'
backtrader-agent --state-root $STATE approval grant --request-id $REQUEST_ID \
  --approver you --confirm
backtrader-agent --state-root $STATE changes apply --manifest $CHANGE_MANIFEST \
  --change-token $CHANGE_TOKEN --idempotency-key key-1
```

## 7. 审批并运行

```bash
backtrader-agent --state-root $STATE approval request --kind run \
  --subject-hash $RUN_SUBJECT --bindings '{"...": "..."}'
backtrader-agent --state-root $STATE approval grant --request-id $REQUEST_ID \
  --approver you --confirm
backtrader-agent --state-root $STATE run --applied-artifact $APPLIED \
  --dataset-manifest dataset-manifest.json --validation-token $VALIDATION_TOKEN \
  --run-token $RUN_TOKEN --mode runonce --idempotency-key run-1
```

模式:`runonce` 与 `runnext`。瞬态失败(超时)把会话置为 `FAILED` 且
`retry_eligible=true`;为同一 effect 重新 request/grant 一次 run 审批即可恢复,
新 RunManifest 会记录 `retry_of` 链。非瞬态失败必须携带修订后的 spec 走
`repair`。

## 8. 读取报告

```bash
backtrader-agent --state-root $STATE report --run-id $RUN_ID --format markdown
backtrader-agent --state-root $STATE compare --left-run-id $R1 --right-run-id $R2
backtrader-agent --state-root $STATE runs list
```

## 参数 sweep

```bash
backtrader-agent --state-root $STATE sweep prepare --session-id session-001 \
  --spec strategy-spec.json --dataset-manifest dataset-manifest.json \
  --param-grid '{"fast_period": [10, 20], "slow_period": [30, 40]}' \
  --engine-root-id engine
backtrader-agent --state-root $STATE approval request --kind sweep \
  --subject-hash $PLAN_HASH --bindings '{"...": "..."}'
backtrader-agent --state-root $STATE approval grant --request-id $REQUEST_ID \
  --approver you --confirm
backtrader-agent --state-root $STATE sweep run --sweep-id $SWEEP_ID \
  --token $SWEEP_TOKEN --max-cells 100 --timeout-per-cell 120
backtrader-agent --state-root $STATE sweep report --sweep-id $SWEEP_ID
```

Sweep 是 **run-only** 能力:cell 从 renderer 拥有的私有草稿执行,绝不写你的
workspace。一次 sweep 审批覆盖整个确定性枚举计划;每个 cell 落独立不可变的
RunManifest/RunResult。每个 archetype 的前 5 参数先验会写入跨会话记忆存储。

## 恢复与状态

```bash
backtrader-agent --state-root $STATE session status --session-id session-001
backtrader-agent --state-root $STATE session recover --session-id session-001
backtrader-agent --state-root $STATE doctor --audit
```

每次转换都记入严格有序、哈希链接的会话日志;checkpoint 原子;恢复只接受
已验证前缀。`doctor --audit` 报告撕裂 journal、`RUNNING` 孤儿、CAS 违规与
过期审批堆积。每笔 CLI 调用都追踪进 `<state>/trace/*.jsonl`(参数值只记
哈希,绝不落原文)。
