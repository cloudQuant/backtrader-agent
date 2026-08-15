# 迭代 001：可信执行与发行契约需求文档

## 1. 背景与问题证据

`backtrader-agent` 的运行流程意图将校验、审批和受控子进程绑定到一个已批准的 Backtrader 引擎。然而当前 CLI 把 `--engine-root-id` 与任意字符串 `--engine-hash` 设为二选一；后者会直接写进 validation token。`ControlledRunner._resolve_engine()` 在没有 root ID 时只回显该字符串并让子进程使用当前 Python 环境。因此用户可完成一个包含任意“引擎哈希”的批准运行，不能证明实际导入的引擎与被批准的引擎一致。

同一审计还发现：

1. 生成的两类运行入口均无条件导入 `backtrader` 和 `pandas`，但包的默认依赖为空，`test` extra 也遗漏 Backtrader；CI 通过手工安装列表绕过发行物元数据。
2. 项目声明 `requires-python = ">=3.8"`，但 CI 矩阵缺少 Python 3.8；本地完整测试已在 3.8 通过，故应把这一支持面持续化。
3. 示例在 `data register --session-id session-001` 前遗漏 `session create`，不能按文档独立走通。
4. `pyproject.toml` 的 GPL 声明与根目录 MIT `LICENSE` 冲突；维护者已确认 MIT 是权威选择，必须把发行元数据对齐并用测试防回归。

## 2. 目标

本迭代交付一个可证明的受控执行契约：所有可执行 validation token 必须来源于一个已注册、只读、可重新检查的 Backtrader 引擎根；运行时必须重新验证该引擎和 Python 执行环境；安装者必须能通过明确的 extras 安装完整运行依赖；CI 必须持续覆盖宣称的最低 Python 版本。

## 3. 需求

### R1：移除调用方伪造的可执行引擎与环境绑定

- `backtrader-agent validate` 必须要求 `--engine-root-id`；不得再接受 `--engine-hash` 或 `--environment-hash` 参数。
- CLI 必须从注册表中调用 `inspect_engine()`，并从当前解释器派生执行环境描述；调用方不能提交哈希字符串代替真实证明。
- validation token 的 bindings 必须同时包含 `engine_root_id`、派生的 `engine_hash` 和派生的 `environment_hash`。
- 所有仍可直接调用 `StrategyValidator` 的内部 API 不得绕过 `ControlledRunner` 的运行前 root/environment 校验。

### R2：引擎根必须可复验且覆盖实际可执行内容

- 引擎描述必须包含 schema 版本、root ID、包名、版本、`__init__.py` 哈希、`version.py` 哈希、受控 `backtrader/` 包树的内容哈希和文件数。
- 包树哈希必须以稳定的相对 POSIX 路径和文件 SHA-256 构建，排除 `__pycache__/` 与 `.pyc`；任何符号链接、非普通文件、根逃逸或缺少必需文件均必须以稳定的 `BTAG-ENGINE-*` 诊断失败。
- 执行前必须重新计算描述并与 token 的引擎哈希一致；校验后改动任一纳入包树的文件必须拒绝运行且不能启动候选子进程。
- 现有 child-process import 路径、版本和 root 归属验证必须保留。

### R3：执行环境必须由运行时派生并在执行前复验

- 新环境描述必须含 schema 版本、解释器绝对路径、Python 版本、实现和平台；它的内容哈希是 `environment_hash` 的唯一来源。
- `run` 在消费 run token 前必须重新派生环境描述；不匹配时返回稳定诊断并保留 token 未消费。
- `doctor` 必须分开报告基础运行时状态与 `execution_ready`：只有至少一个有效、只读 registered engine root 且相应依赖可用时，执行才可就绪。

### R4：发行物安装契约必须真实可消费

- 基础包继续不声明第三方强制依赖，使 `doctor`、`payload`、根注册、会话和静态能力保持 offline-first。
- `pyproject.toml` 必须提供 `backtest` extra（`backtrader` 和 `pandas`）、`single-test` extra（`pytest`）以及可运行完整测试的 `test` extra（backtest、pytest、jsonschema、build 工具）。
- `doctor` 和运行前预检必须报告缺少的 profile 依赖；`python_bundle` 需要 Backtrader/Pandas，`single_test` 额外需要 pytest。缺依赖时不得启动候选子进程或消费 token。
- README、中文 README 区段、CONTRIBUTING、CI 和 clean-wheel 测试必须消费同一 extra 契约，而非保留独立的手工依赖列表。

### R5：支持矩阵和 CI 必须与发行声明一致

- CI 至少覆盖 Python 3.8 和 Python 3.12；两者均执行从 `.[test]` 安装后的单元测试、独立性审计、doctor 和 manifest 新鲜度检查。
- 完整 14-cell acceptance 继续在 Python 3.12 执行，以限制总 CI 时长但保持完整的运行行为门。
- wheel 元数据测试必须断言 extras 名称和每个 required distribution 都存在，防止未来回归成空依赖。

### R6：文档与可追溯性

- 示例必须在引用 `session-001` 前创建它，并明确使用 `backtrader-agent[backtest]` 安装受控运行能力。
- README 的英文和中文工作流必须明确：可执行 validation 只接受已注册、只读引擎根；引擎和环境哈希由产品派生。
- 每次改动 source、资源、测试、文档或 CI 后必须重生成根和包 distribution manifests。

### R7：MIT 许可证元数据必须与发行物一致

- `pyproject.toml` 必须声明 MIT，与根目录现有 `LICENSE` 文本一致；不修改许可证文本或发布版本。
- wheel metadata 测试必须断言许可证声明为 MIT，README 与最终报告不得再把许可证选择描述为未决。

## 4. 非功能约束

- 保持 Python 3.8+ 兼容，生产运行时只使用标准库；不得为本迭代引入新的强制第三方库。
- 保持现有安全不变量：不在宿主导入候选策略，不使用动态执行，不使用 `shell=True`，不接触网络/券商/实盘。
- 不弱化双审批、令牌一次性消费、idempotency、CAS、路径约束、会话哈希链或独立性审计。
- 只修改与本轮契约有关的代码、测试、CI、文档和 manifests；许可证文本保持不变，许可元数据按维护者确认的 MIT 对齐。

## 5. 需求到验收追踪

| 需求 | 主验收证据 |
| --- | --- |
| R1 | CLI raw-hash 参数被拒；root-bound workflow 成功；token bindings 是派生值 |
| R2 | 引擎树改动、符号链接和 child import 不匹配均拒绝；合法 14-cell 仍通过 |
| R3 | 环境变动在 token 消费前拒绝；doctor 分开报告 readiness |
| R4 | wheel `METADATA` extras 断言；缺依赖预检无副作用；extras consumer 成功 |
| R5 | CI 文件覆盖 3.8/3.12；本地 3.8 和 3.12 通过规定门 |
| R6 | 示例 smoke 测试与文档链接检查；manifests 新鲜 |
| R7 | `pyproject.toml` 与 wheel metadata 都声明 MIT，且与 `LICENSE` 一致 |
