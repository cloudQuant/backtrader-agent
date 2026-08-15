# 迭代 013 任务级实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 分四阶段完成工具面契约、eval harness、sweep/重试、可观测性/记忆、分析器/Sizers、指标注册表/Timers,每阶段独立可验收。

**Architecture:** 本计划按仓库既有模式改造:CLI 输出统一 envelope;新增 `archetypes.py`/`adapters.py` 注册表单源;新增 `sweep.py`(run-only 能力)、`observability.py`、`memory.py`、`caching.py`;`runner.py`/`changes.py` 拆包并保持公开符号再导出。

**Tech Stack:** Python 3.8+、argparse、pytest、无新强依赖(标准库实现 sweep/cache/trace/memory)。

**Spec:** `docs/iterations/iteration-013-agentic-engineering-and-quant-capabilities/{requirements,design,acceptance}.md` — 本计划从 spec 出发论证,执行者需同时阅读。

## Global Constraints

- Python 3.8+ 兼容、POSIX/Windows、MIT 许可证;不引入新强依赖。
- 审批/安全模型不变弱:sweep 是 run-only 能力;安全敏感哈希(engine 树、feed 文件)只做进程内缓存,禁止跨进程持久缓存。
- 输出纪律:成功 `{"status": "ok", "result": ...}`;失败 `{"status": "failed", "diagnostic": ...}`;exit code 0/2/3/4(成功/用法/BTAG 领域/OSError)。
- BTAG-* 诊断不泄露 secret 与绝对 target 路径;stderr 上的 warning 不破坏 stdout JSON。
- 既有发行门全程保持绿(pytest、ruff、black、audit_independence、doctor、run_acceptance 14-cell、分发契约);每任务结束跑其相关测试 + `ruff check` + `black --check`。
- 提交惯例:`<type>: <description>`,type ∈ feat/fix/refactor/docs/test/chore。
- 测试惯例:pytest + type annotations + frozen dataclass;安全模型改动必须附 red test(伪造/重放/越界/越权)。

## 文件结构总览

```
src/backtrader_agent/
├── cli.py                # envelope、exit codes、inline JSON、actions --json、sweep 子命令、trace 钩子
├── archetypes.py  (新)   # 7 archetype 单源注册表(契约值/模板/允许参数)
├── adapters.py    (新)   # 6 data adapter 单源注册表(格式/列名/装配路径)
├── caching.py     (新)   # 进程内 memoize(安全哈希)+ catalog manifest 级验证
├── sweep.py       (新)   # SweepPlan 记录、cell 渲染、sweep-result-v1
├── observability.py (新) # dispatch trace JSONL
├── memory.py      (新)   # 跨会话记忆存储(datasets/params)
├── runner/        (新)   # runner.py 拆分(profiles/execute/reports/resume),保留再导出
├── changes/       (新)   # changes.py 拆分(prepare/apply/rollback),保留再导出
├── contracts.py         # archetype/adapter 枚举改为从注册表派生;sizing/timers/cheat 可选区块
├── scaffold.py          # 模板从注册表取;_single_test_source 改模板函数;sizing 渲染段
├── validator.py         # sizer/timer/cheat 白名单扩展
├── sessions.py          # FAILED→RUN_APPROVED 迁移 + retry_eligible 门
├── tokens.py            # REQUIRED_BINDINGS 加 sweep;expected_bindings 助手
├── doctor.py            # --audit 状态根审计
├── catalog.py           # snapshot_hash 级验证;--snapshot-path;--kind indicator
└── resources/
    ├── actions-v1.json / contracts/actions-v1.schema.json  (新)
    ├── agent-payload.md (重写:worked trace + 恢复表 + 压缩规则 + version 字段)
    └── catalog/indicator-registry-v1.json (新)
tests/
├── evals/harness.py, graders.py, tasks/*.json (新)
├── test_cli_contract.py / test_cache_semantics.py / test_sweep.py /
│   test_run_retry.py / test_observability.py / test_memory_store.py (新)
scripts/
├── run_evals.py, eval_llm_loop.py, extract_indicator_registry.py (新)
```

---

## Phase 0:工具面契约与工程健康

### Task 1:统一成功 envelope(R1)

**Files:**
- Modify: `src/backtrader_agent/cli.py:519-548`(`main`)
- Test: `tests/test_cli_contract.py`(新)

**Interfaces:**
- Consumes: `dispatch(args) -> Dict[str, Any]`(不变)
- Produces: `main()` 成功输出 `{"status": "ok", "result": <dispatch 返回值>}`;`_emit` 保持原样(仅序列化)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_cli_contract.py
import json
from backtrader_agent import cli


def test_success_envelope_wraps_result(capsys, tmp_path):
    state = tmp_path / "state"
    code = cli.main(["--state-root", str(state), "doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["status"] == "ok"
    assert payload["result"]["status"] == "ready"  # doctor 原输出移入 result


def test_failure_envelope_unchanged(capsys, tmp_path):
    code = cli.main(["--state-root", str(tmp_path / "s"), "report", "--run-id", "run-0" * 2])
    payload = json.loads(capsys.readouterr().out)
    assert code != 0
    assert payload["status"] == "failed"
    assert payload["diagnostic"]["code"].startswith("BTAG-")
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_cli_contract.py -v`
Expected: FAIL — 当前输出无外层 `{"status": "ok", ...}`。

- [ ] **Step 3: 最小实现**

`main()` 成功路径改为:先提取 warnings(原有逻辑),再输出

```python
    for warning in ...:  # 原有 warning 提取不变,作用于 result
        print("WARNING: {}".format(warning), file=sys.stderr)
    _emit({"status": "ok", "result": result})
    return 0
```

- [ ] **Step 4: 运行确认通过 + 全量迁移**

Run: `pytest tests/test_cli_contract.py -v`(PASS);再 `pytest tests -q -p no:cacheprovider`。
迁移:全部既有测试中断言裸输出的位置加 `["result"]` 层(逐个文件修正,禁止改实现迁就测试)。

- [ ] **Step 5: 提交**

```bash
git add tests/test_cli_contract.py src/backtrader_agent/cli.py tests/
git commit -m "feat: wrap all CLI success output in a uniform status envelope"
```

### Task 2:退出码区分(R2/R5)

**Files:**
- Modify: `src/backtrader_agent/cli.py:519-538`(`main` 异常分层)
- Test: `tests/test_cli_contract.py`

**Interfaces:**
- Produces:`AgentError → exit 3`;`OSError → exit 4` + `BTAG-CLI-IO`;`ValueError/JSONDecodeError → exit 3` + `BTAG-CLI-INPUT`;用法错误保持 argparse 的 exit 2。

- [ ] **Step 1: 写失败测试**

```python
def test_exit_code_domain_error(monkeypatch, capsys):
    def boom(args):
        raise cli.AgentError("BTAG-TEST", "boom")
    monkeypatch.setattr(cli, "dispatch", boom)
    assert cli.main(["doctor", "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["diagnostic"]["code"] == "BTAG-TEST"


def test_exit_code_io_error(monkeypatch, capsys):
    def boom(args):
        raise OSError("disk full")
    monkeypatch.setattr(cli, "dispatch", boom)
    assert cli.main(["doctor", "--json"]) == 4
    payload = json.loads(capsys.readouterr().out)
    assert payload["diagnostic"]["code"] == "BTAG-CLI-IO"
    assert payload["diagnostic"]["severity"] == "error"


def test_exit_code_input_error(monkeypatch, capsys):
    def boom(args):
        raise ValueError("bad json")
    monkeypatch.setattr(cli, "dispatch", boom)
    assert cli.main(["doctor", "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["diagnostic"]["code"] == "BTAG-CLI-INPUT"
```

- [ ] **Step 2: 运行确认失败** — Expected: `AgentError` 返回 2(旧行为),其余失败。

- [ ] **Step 3: 实现** — `main()` 的 except 链改为:

```python
    except AgentError as exc:
        _emit({"status": "failed", "diagnostic": exc.as_dict()})
        return 3
    except OSError as exc:
        _emit({"status": "failed", "diagnostic": {
            "code": "BTAG-CLI-IO", "severity": "error",
            "message": "runtime I/O failure: {}".format(exc.__class__.__name__)}})
        return 4
    except (ValueError, json.JSONDecodeError):
        ...  # 保持 BTAG-CLI-INPUT,返回 3
```

- [ ] **Step 4: 运行确认通过** + 全量回归。
- [ ] **Step 5: 提交** `fix: distinguish exit codes and stop mislabeling OSError as input errors`

### Task 3:内联 JSON / @file 输入(R4)

**Files:**
- Modify: `src/backtrader_agent/cli.py:31-36`(`_json_file` 旁新增 `_json_load`);替换 `dispatch` 中全部 `_json_file(args.*)` 调用点(cli.py:290-491,约 12 处)
- Test: `tests/test_cli_contract.py`

**Interfaces:**
- Produces: `_json_load(value: str) -> Any` — `@` 前缀读文件;否则先 `json.loads` 内联,解析失败按路径读文件;路径读取 OSError 直接上抛(由 Task 2 映射为 exit 4)。

- [ ] **Step 1: 写失败测试**

```python
def test_json_load_inline_and_file_equivalent(tmp_path):
    path = tmp_path / "s.json"
    path.write_text('{"a": 1}', encoding="utf-8")
    assert cli._json_load('{"a": 1}') == {"a": 1}
    assert cli._json_load("@" + str(path)) == {"a": 1}
    assert cli._json_load(str(path)) == {"a": 1}


def test_json_load_missing_file_raises_oserror(tmp_path):
    with pytest.raises(OSError):
        cli._json_load(str(tmp_path / "nope.json"))
```

- [ ] **Step 2: 运行确认失败** — `_json_load` 未定义。

- [ ] **Step 3: 实现**

```python
def _json_load(value: str) -> Any:
    if value.startswith("@"):
        return _json_file(value[1:])
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return _json_file(value)
    if not isinstance(parsed, dict):
        raise AgentError("BTAG-CLI-JSON", "input JSON must be an object")
    return parsed
```

`dispatch` 内 `_json_file(args.spec)` 等全部替换为 `_json_load(args.spec)`(`changes prepare --files` 是 JSON 数组,改用 `json.loads(args.files)` + 数组校验,不经过 dict 检查)。

- [ ] **Step 4: 运行确认通过** + 全量回归(既有测试传文件路径,行为不变)。
- [ ] **Step 5: 提交** `feat: accept inline JSON or @file for every file-typed CLI argument`

### Task 4:机器可读 action schema(R3)

**Files:**
- Modify: `src/backtrader_agent/cli.py:102-260`(解析器旁新增反射函数与 `actions` 子命令)
- Create: `src/backtrader_agent/resources/actions-v1.json`、`src/backtrader_agent/resources/contracts/actions-v1.schema.json`
- Modify: `scripts/build_manifest.py`(收录新资源)
- Test: `tests/test_cli_contract.py` + `tests/test_distribution_contracts.py` 追加

**Interfaces:**
- Produces: `build_action_schema(parser) -> Dict[str, Any]`,结构 `{"schema_version": "actions-v1", "actions": {"doctor": {...}, "data register": {"params": [{"name", "option_strings", "required", "type", "choices", "default", "help"}], "help": ...}, ...}}`;`actions --json` 输出与其逐字节一致。

- [ ] **Step 1: 写失败测试**

```python
import importlib.resources as pkg_resources


def test_action_schema_matches_packaged_resource():
    parser = cli.build_parser()
    schema = cli.build_action_schema(parser)
    packaged = json.loads(
        pkg_resources.files("backtrader_agent.resources").joinpath("actions-v1.json").read_text("utf-8")
    )
    assert schema == packaged


def test_action_schema_covers_required_params():
    schema = cli.build_action_schema(cli.build_parser())
    register = schema["actions"]["data register"]["params"]
    by_name = {p["name"] for p in register}
    assert "spec" in by_name and "session_id" in by_name
    assert all(p["required"] for p in register if p["name"] in {"spec", "session_id"})


def test_actions_command_emits_schema(capsys, tmp_path):
    code = cli.main(["--state-root", str(tmp_path / "s"), "actions", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["result"]["schema_version"] == "actions-v1"
```

- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现** — 递归收集:`parser._subparsers` 的 `.choices` 映射子命令名 → 子 parser;每层把路径拼成 `"data register"` 形式;参数从 `subparser._actions` 取 `dest/option_strings/required/type`(用 `action.type.__name__` 或 `None`)/`choices/default/help`。生成 `resources/actions-v1.json` 用一个小脚本固化(可放 `scripts/build_manifest.py` 内);schema 文件手写,校验顶层与 params 字段。`actions --json` 走 dispatch 直接返回 schema dict。
- [ ] **Step 4: 运行确认通过**;`python scripts/build_manifest.py` 确认 manifest 收录新资源;分发契约测试绿。
- [ ] **Step 5: 提交** `feat: emit a machine-readable action schema for host tool generation`

### Task 5:注册表单源(R6)

**Files:**
- Create: `src/backtrader_agent/archetypes.py`、`src/backtrader_agent/adapters.py`
- Modify: `src/backtrader_agent/contracts.py:10-18`(ARCHETYPES)、`scaffold.py:61-134`(ARCHETYPE_CODE)、`catalog.py:13-21`(ARCHETYPES)、`data.py:23-97`(ALLOWED_FORMATS/DEFAULT_COLUMN_NAMES)、`contracts.py:255-264`(DatasetManifest allowlist)
- Test: `tests/test_registry_consistency.py`(新)

**Interfaces:**
- Produces:
  - `archetypes.ARCHETYPE_SPECS: Dict[str, ArchetypeSpec]`,`ArchetypeSpec = NamedTuple("ArchetypeSpec", [("contract_value", str), ("template", str), ("allowed_params", Tuple[str, ...])])`;`archetypes.ARCHETYPE_IDS: FrozenSet[str]`
  - `adapters.ADAPTER_SPECS: Dict[str, AdapterSpec]`,`AdapterSpec = NamedTuple("AdapterSpec", [("format", str), ("default_columns", Tuple[Tuple[str, int], ...]), ("assembly", str)])`;`adapters.ADAPTER_FORMATS: FrozenSet[str]`

- [ ] **Step 1: 写失败测试**

```python
from backtrader_agent import adapters, archetypes, catalog, contracts, data, scaffold


def test_archetype_registry_is_single_source():
    assert contracts.ARCHETYPES == archetypes.ARCHETYPE_IDS
    assert set(scaffold.ARCHETYPE_CODE) == archetypes.ARCHETYPE_IDS
    assert catalog.ARCHETYPES == archetypes.ARCHETYPE_IDS


def test_adapter_registry_is_single_source():
    assert data.ALLOWED_FORMATS == adapters.ADAPTER_FORMATS
    assert set(contracts.ALLOWED_DATASET_FORMATS) == adapters.ADAPTER_FORMATS
    assert "canonical_csv_v1" not in contracts.ALLOWED_DATASET_FORMATS  # 修复不一致
```

(注:`contracts.py:255-264` 的实际 allowlist 符号名以源码为准;测试按真实符号名调整。)

- [ ] **Step 2: 运行确认失败** — `canonical_csv_v1` 不一致与多份枚举存在。

- [ ] **Step 3: 实现** — 把 7 个 archetype 的模板源码与允许参数、6 个 adapter 的列定义与装配路径搬进注册表;`contracts/scaffold/catalog/data` 改为 `from .archetypes import ...` / `from .adapters import ...` 派生;删除旧枚举与 `canonical_csv_v1`。
- [ ] **Step 4: 运行确认通过** + 全量回归(渲染/校验/注册行为不变)。
- [ ] **Step 5: 提交** `refactor: unify archetype and adapter definitions into single-source registries`

### Task 6:缓存纪律(R7)

**Files:**
- Create: `src/backtrader_agent/caching.py`
- Modify: `src/backtrader_agent/engines.py:39-86`(树哈希)、`src/backtrader_agent/runner.py:459-569`(feed 哈希/探测)、`src/backtrader_agent/catalog.py:112-152`(逐条验证改 manifest 级)
- Test: `tests/test_cache_semantics.py`(新)

**Interfaces:**
- Produces:`caching.memoized(fn)` 装饰器(进程内 dict,key 取 `(fn.__qualname__, args, kwargs)`);`catalog.verify_snapshot_once(path) -> None`(读 snapshot 文件 + manifest 的 `snapshot_hash` 单次 SHA-256 比对,替代逐条 entry 验证)。

- [ ] **Step 1: 写失败测试**

```python
from backtrader_agent import caching, catalog, engines


def test_engine_hash_computed_once_per_process(monkeypatch, tmp_path):
    engine = tmp_path / "engine"
    (engine / "backtrader").mkdir(parents=True)
    (engine / "backtrader" / "__init__.py").write_text("__version__ = '1.3.0'\n")
    (engine / "backtrader" / "version.py").write_text("__version__ = '1.3.0'\n")
    calls = []
    real = engines._tree_hash
    monkeypatch.setattr(engines, "_tree_hash", lambda p: (calls.append(str(p)) or real(p)))
    h1 = engines._tree_hash(engine)
    h2 = engines._tree_hash(engine)  # 同进程第二次走缓存
    assert h1 == h2
    assert len(calls) == 1


def test_no_persistent_cache_for_security_hashes(tmp_path):
    state = tmp_path / "state"
    # 调用一次 engine 检查/哈希后,断言 state 下无 cache 目录
    assert not (state / "cache").exists()


def test_catalog_verifies_snapshot_hash_not_each_entry(monkeypatch):
    hits = []
    real = catalog.SnapshotCatalog._verify_entry
    monkeypatch.setattr(catalog.SnapshotCatalog, "_verify_entry",
                        lambda self, e: (hits.append(e) or real(self, e)))
    catalog.SnapshotCatalog().search("sma crossover", top_k=3)
    assert hits == []  # 不再逐条验证;由 verify_snapshot_once 兜底
```

(符号名 `_tree_hash`/`_verify_entry` 以实施时源码实际命名为准,测试随源码调整。)

- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现** — `memoized` 装饰器(dict + `hashable kwargs`);`engines` 树哈希、`runner` 的 feed 哈希与探测结果加装饰(仅进程内;不落盘);`catalog` 构造时 `verify_snapshot_once`,删除逐条 `_verify_entry` 调用。
- [ ] **Step 4: 运行确认通过** + 全量回归 + `scripts/run_acceptance.py` 冒烟。
- [ ] **Step 5: 提交** `perf: memoize security hashes per-process and verify catalog at manifest level`

### Task 7:拆分与死代码(R8)

**Files:**
- Create: `src/backtrader_agent/runner/{__init__,profiles,execute,reports,resume}.py`(原 `runner.py` 980 行拆分,`__init__.py` 再导出 `ControlledRunner`、`list_runs`)
- Create: `src/backtrader_agent/changes/{__init__,prepare,apply,rollback}.py`(原 `changes.py` 816 行拆分,再导出 `ChangeManager`)
- Modify: `src/backtrader_agent/tokens.py:24-60`(新增 `expected_bindings(kind, **context) -> Dict[str, str]` 助手;`runner`/`changes` 的手写绑定字典调用点统一引用)
- Modify: `src/backtrader_agent/scaffold.py:418-430`(`_single_test_source` 改模板函数 `_render_single_test_source(strategy_source: str) -> str`,`str.format` 占位符替代 `str.replace`)
- Modify: `src/backtrader_agent/cli.py:111`(`doctor --json` 保留参数、文档注明输出恒为 JSON)、`cli.py:328-338` + `catalog.py:106-107`(`search`/`inspect` 增加 `--snapshot-path` 可选参数,传入 `SnapshotCatalog(snapshot_path=...)`)
- Test: `tests/test_scaffold_validator_catalog.py` 追加 golden 测试;全量回归

- [ ] **Step 1: 写失败测试**

```python
def test_single_test_source_template_golden():
    src = scaffold._render_single_test_source("class Demo(bt.Strategy):\n    pass\n")
    assert "class Demo(bt.Strategy)" in src
    assert "BACKTRADER_AGENT_RESULT" in src
    assert "strategy_source" not in src  # 不残留占位符


def test_catalog_search_uses_explicit_snapshot_path(tmp_path):
    from backtrader_agent import cli
    code = cli.main(["--state-root", str(tmp_path / "s"), "catalog", "search",
                     "--query", "sma", "--snapshot-path", str(tmp_path / "snap.jsonl")])
    assert code in (0, 3)  # 参数被接受;空快照允许 BTAG 领域错误,不允许用法错误(2)
```

- [ ] **Step 2: 运行确认失败** — `_render_single_test_source` 不存在、`--snapshot-path` 未定义。

- [ ] **Step 3: 实现** — 拆分两个大模块(纯移动 + 再导出,禁止同时改逻辑);绑定字典助手与调用点替换;模板函数化;`--snapshot-path` 接线;`rm -rf build/`(确认 `.gitignore` 已覆盖 `build/`)。
- [ ] **Step 4: 运行确认通过** + 三解释器回归 + `python scripts/build_manifest.py` 重生成 manifest。
- [ ] **Step 5: 提交** `refactor: split runner/changes modules, template-ize single_test source, wire catalog snapshot-path`

### Phase 0 门

跑:三解释器 pytest、ruff、black、`audit_independence.py`、`doctor`、`run_acceptance.py`、分发契约。对照 acceptance.md 的 A0-1 至 A0-8 逐项回填证据。

---

## Phase 1:工程轨

### Task 8:payload 重写 + 提示词版本化(R12/R13)

**Files:**
- Modify: `src/backtrader_agent/resources/agent-payload.md`、`adapters/*`、`SKILL.md`(仓库根副本,与 payload 保持字节一致)
- Create: `docs/evals/payload-changelog.md`
- Test: `tests/test_payload_contract.py`(新)

**Interfaces:**
- Produces: payload 含 `version: "13.0.0"`、完整 worked trace、BTAG 恢复表、压缩规则;`cli.PAYLOAD_PATH: Path` 模块级常量(从 dispatch 的 payload 分支提取,`dispatch` 与测试共用)

- [ ] **Step 1: 写失败测试**

```python
import re
from backtrader_agent import cli
from backtrader_agent.canonical import sha256_bytes

EXPECTED_PAYLOAD_SHA256 = "<重写完成后的实际 sha256,与 version 一同 bump>"  # 首次 RED 用占位失败值


def test_payload_content_hash_pinned():
    content = cli.PAYLOAD_PATH.read_text(encoding="utf-8")
    assert sha256_bytes(content.encode("utf-8")) == EXPECTED_PAYLOAD_SHA256


def test_payload_has_version():
    content = cli.PAYLOAD_PATH.read_text(encoding="utf-8")
    assert re.search(r"^version:\s*\"13\.0\.0\"", content, re.M)


def test_payload_menu_rows_point_to_real_commands(capsys, tmp_path):
    cli.main(["--state-root", str(tmp_path / "s"), "actions", "--json"])
    schema = json.loads(capsys.readouterr().out)["result"]["actions"]
    content = cli.PAYLOAD_PATH.read_text(encoding="utf-8")
    # 提取菜单表路由列中的命令词,断言每个都在 schema 中或以 --help 结尾
    for cmd in re.findall(r"`([a-z][a-z-]+)`", content):
        top = cmd.split()[0]
        assert top in schema or cmd in ("--help",)
```

- [ ] **Step 2: 运行确认失败** — payload 无 version、无 worked trace。
- [ ] **Step 3: 实现** — 重写 payload:
  1. 头部加 `version: "13.0.0"`(注释行说明变更必须 bump);
  2. "Worked trace" 小节:9 条逐字命令(doctor → roots register → session create → data inspect/register → spec --approve → draft → validate → changes prepare → approval request/grant → changes apply → approval request/grant --kind run → run → report),附最小 DataSpec/StrategySpec JSON;
  3. "BTAG 恢复表":`BTAG-TOKEN-EXPIRED`→重新 validate + 重新 request/grant;`BTAG-STATE-TRANSITION`→`session status` 核对当前状态再决定 repair/重试;`BTAG-CHANGE-PREIMAGE`→目标文件已被外部修改,停止并报告;`BTAG-RUN-TIMEOUT`→(R14 落地后)同 effect 重试;`BTAG-CLI-INPUT`→检查 JSON 语法;`BTAG-CLI-IO`→检查磁盘/权限;
  4. "压缩规则":被 hash/token 固定的 artifact 可安全摘要;draft 路径与未消费 token 不可丢弃;
  5. "数据集复用":NW 入口先 `data list`,仅新数据走 register。
  同步更新 `SKILL.md`(字节一致)、四个 adapter 的调用指引。完成后算 sha256 回填 `EXPECTED_PAYLOAD_SHA256`(Step 1 已写占位,此处为 GREEN 步骤),并写 `docs/evals/payload-changelog.md` 首条记录。
- [ ] **Step 4: 运行确认通过**;`payload` 命令冒烟 + 仓库根/打包副本字节一致测试(`test_runner_installer_audit.py` 既有)。
- [ ] **Step 5: 提交** `docs: rewrite agent payload with worked trace, recovery table, and versioning`

### Task 9:eval harness 引擎(R9)

**Files:**
- Create: `tests/evals/harness.py`、`tests/evals/graders.py`、`tests/evals/__init__.py`
- Create: `scripts/run_evals.py`
- Test: `tests/test_eval_harness.py`(新)

**Interfaces:**
- Consumes: 已安装的 `backtrader-agent` CLI(子进程驱动)、`tests/evals/tasks/*.json`
- Produces:
  - `harness.run_task(task: Dict[str, Any], state_root: Path, env: Dict[str, str]) -> TaskResult`(`TaskResult = NamedTuple("TaskResult", [("task_id", str), ("steps", List[StepResult]), ("passed", bool)])`)
  - `graders.GRADERS: Dict[str, Callable]`,key ∈ `exit_code/envelope/schema/hash_eq/file_exists/json_path_eq`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_eval_harness.py
from tests.evals import harness

TASK = {
    "task_id": "smoke-doctor",
    "intent": "diagnose the environment",
    "fixture": None,
    "steps": [
        {"argv": ["doctor", "--json"],
         "expect": {"exit_code": 0, "status": "ok", "json_path_eq": {"result.status": "ready"}}}
    ],
}


def test_run_task_returns_passed_result(tmp_path):
    result = harness.run_task(TASK, tmp_path / "state", {})
    assert result.passed is True
    assert len(result.steps) == 1
```

- [ ] **Step 2: 运行确认失败** — `tests.evals` 不存在。

- [ ] **Step 3: 实现** — `harness.run_task`:`subprocess.run([sys.executable, "-m", "backtrader_agent", *step["argv"], "--state-root", str(state_root)], capture_output=True, env=env, timeout=300)`;对每步断言:`exit_code`(比对 returncode)、`status`(JSON 顶层)、`json_path_eq`(点路径取值相等)、`hash_eq`(文件 sha256)、`file_exists`;全过才 `passed=True`。`run_evals.py`:扫描 `tests/evals/tasks/*.json`,逐个跑,汇总 `{passed, failed, total}`,非零失败退出码 1。
- [ ] **Step 4: 运行确认通过**;`python scripts/run_evals.py` 全绿。
- [ ] **Step 5: 提交** `test: add deterministic scripted-host eval harness and runner`

### Task 10:eval 任务集(R9/R10)

**Files:**
- Create: `tests/evals/tasks/*.json`(24 个任务)
- Modify: `.github/workflows/ci.yml`(新 job:`python scripts/run_evals.py`)

任务清单(每个按 Task 9 schema 编写;fixture CSV 复用 `tests/` 既有 fixture 或按 `tests/helpers.py` 模式生成):

| task_id | 内容 | 关键断言 |
| --- | --- | --- |
| smoke-doctor | doctor | exit 0 + envelope |
| smoke-actions | actions --json | schema 校验 |
| pipeline-single-data-indicator | 完整管线(单数据 SMA 交叉,`single_test` 与 `python_bundle` 各一) | 每步 envelope;run 后 `runs list` 有记录;report 可读 |
| pipeline-<archetype> × 6 | 其余 6 archetype 完整管线 | 同上(7 archetype 全覆盖) |
| register-<adapter> × 6 | 6 adapter 数据注册 | `data list` 出现对应 dataset_id |
| replay-idempotency | 同幂等键二次 run | 返回已记录结果,不新建 run |
| inject-expired-token | apply 前用过期 change token | `BTAG-TOKEN-*` + 恢复表路径(重新 prepare→request→grant→apply) |
| inject-preimage | apply 前外部篡改目标文件 | `BTAG-CHANGE-PREIMAGE` + 停止语义 |
| inject-unapproved-run | 未经 run approval 直接 run | 拒绝 + 按恢复表补齐 approval |
| inject-corrupt-journal | 追加畸形后缀后 `session recover` | 隔离后缀、会话 `PAUSED`/可恢复 |
| sweep-smoke(Phase 1 功能轨完成后加) | 2×2 网格 sweep | 4 cell 结果 + 排名正确 |

- [ ] **Step 1: 先写 smoke-doctor、pipeline-single-data-indicator、inject-expired-token 三个任务,跑 harness 确认可行(RED 概念:任务驱动真实 CLI 首次通过前,harness 可能暴露 payload/CLI 断点)。**
- [ ] **Step 2: 补齐其余 21 个任务,全部通过。**
- [ ] **Step 3: CI 接线** — ci.yml 追加 job,依赖安装步骤一致,跑 `python scripts/run_evals.py`。
- [ ] **Step 4: 提交** `test: add 24-task deterministic eval suite and wire it into CI`

### Task 11:opt-in LLM 在环门(R11)

**Files:**
- Create: `scripts/eval_llm_loop.py`
- Modify: `docs/evals/payload-changelog.md`(说明 LLM 门用法)
- Test: `tests/test_eval_harness.py` 追加(skip 语义)

**Interfaces:**
- Produces: `scripts/eval_llm_loop.py` — 读 `BACKTRADER_AGENT_EVAL_API_KEY` 与 `BACKTRADER_AGENT_EVAL_MODEL`(缺省 `claude-fable-5`);缺失即打印 skip 并退出 0;存在时用 Anthropic SDK(作为 dev 依赖,不进 runtime 依赖)对任务集子集逐任务执行 3 次完整工作流,统计 pass@1/pass@3 写 `docs/evals/<版本>-llm-loop.log`。

- [ ] **Step 1: 写失败测试**

```python
def test_llm_loop_skips_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("BACKTRADER_AGENT_EVAL_API_KEY", raising=False)
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "eval_llm_loop.py"), "--tasks", "smoke-doctor"],
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0
    assert "skip" in result.stdout.lower()
```

- [ ] **Step 2: 运行确认失败** — 脚本不存在。
- [ ] **Step 3: 实现** — 脚本骨架:`argparse(--tasks 过滤器)` → 无 key 则 skip → 有 key 则对每个任务:构造系统提示(读取 payload + 任务 intent)→ 循环调用 API 驱动 CLI(函数调用方式),3 次尝试,p@k 统计 → 写日志。SDK 依赖放 `pyproject.toml` 的 dev extra(`eval`),不进运行时。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: 提交** `feat: add opt-in LLM-in-the-loop eval gate`

---

## Phase 1:功能轨

### Task 12:瞬态失败重试(R14)

**Files:**
- Modify: `src/backtrader_agent/sessions.py:28-40`(`TRANSITIONS["FAILED"]` 加 `"RUN_APPROVED"`;manifest 加 `retry_eligible` 字段;`transition()` 对 `FAILED→RUN_APPROVED` 强制 `retry_eligible` 门)
- Modify: `src/backtrader_agent/runner.py`(失败分类:瞬态白名单 `TRANSIENT_FAILURE_CODES = frozenset({"BTAG-RUN-TIMEOUT"})` + 实施时确认的 OS 资源类码;失败时写 `retry_eligible`;RunManifest 加 `retry_of`)
- Test: `tests/test_run_retry.py`(新)

**Interfaces:**
- Consumes: `SessionStore.transition(session_id, to_state, action_type, input_hashes, effect_references=..., retry_eligible=...)`(扩展可选参数)
- Produces: `ControlledRunner.TRANSIENT_FAILURE_CODES`;RunManifest 可选 `retry_of`

- [ ] **Step 1: 写失败测试(red tests 先行)**

```python
def _fail_run_with(session, code, monkeypatch):
    # 构造:完成到 RUN_APPROVED 的会话,monkeypatch runner 子进程使失败码为 code
    ...


def test_transient_failure_allows_same_effect_retry(tmp_path):
    session = _fail_run_with(..., "BTAG-RUN-TIMEOUT")
    # 同 subject/effect 再次 run 走通;RunManifest.retry_of 指向前一个 run id
    assert new_manifest["retry_of"] == first_run_id


def test_non_transient_failure_rejects_retry(tmp_path):
    session = _fail_run_with(..., "BTAG-RUN-FAILED")
    with pytest.raises(AgentError) as exc:
        retry_run(session)
    assert exc.value.code == "BTAG-STATE-TRANSITION"


def test_changed_effect_rejects_retry(tmp_path):
    # 换 dataset 后同 session 重试,subject hash 不同 → 拒绝
    ...


def test_archived_session_never_retries(tmp_path):
    # session archive 后,FAILED→RUN_APPROVED 被拒
    ...
```

- [ ] **Step 2: 运行确认失败** — 迁移不存在。

- [ ] **Step 3: 实现** — `sessions.py`:
```python
TRANSITIONS = {..., "FAILED": {"REPAIRING", "CANCELLED", "RUN_APPROVED"}, ...}

def transition(self, session_id, to_state, action_type, input_hashes,
               effect_references=None, retry_eligible=None):
    manifest = self._load_manifest(session_id)  # 既有加载逻辑
    if manifest["state"] == "FAILED" and to_state == "RUN_APPROVED":
        if not manifest.get("retry_eligible"):
            raise AgentError("BTAG-STATE-TRANSITION",
                             "retry requires a transient failure of the same effect")
    ...
```
失败时:`runner` 捕获瞬态码 → `transition(..., "FAILED", ..., retry_eligible=True)`;run 成功入口在 `RUN_APPROVED` 前校验 subject 与上次失败 run 的 effect 一致(复用 `compute_run_subject`),不一致拒绝。`retry_of` 写入新 RunManifest。

- [ ] **Step 4: 运行确认通过**;全量回归。
- [ ] **Step 5: 提交** `feat: allow same-effect retry after transient run failures`

### Task 13:SweepPlan 与 sweep prepare(R15)

**Files:**
- Create: `src/backtrader_agent/sweep.py`
- Modify: `src/backtrader_agent/cli.py:102-260`(`sweep` 子命令组:prepare/run/report)
- Test: `tests/test_sweep.py`(新)

**Interfaces:**
- Consumes: `StrategySpec.from_dict`、`hash_object`、`ArtifactRenderer`、`StrategyValidator`、`ControlledRunner`
- Produces:
  - `sweep.prepare_sweep(state: Path, session_id: str, spec: StrategySpec, dataset_manifest: Dict[str, Any], param_grid: Dict[str, List[float]]) -> Dict[str, Any]`
  - 持久化:`<state>/sweeps/sweep_<64hex>/sweep-plan.json`,字段 `{schema_version: "sweep-plan-v1", sweep_id, session_id, spec_hash, dataset_manifest_hash, cells: [{cell_id: "cell_<16hex>", params: {...}, cell_hash}]}`

- [ ] **Step 1: 写失败测试**

```python
def test_sweep_prepare_enumerates_grid(tmp_path, make_approved_spec):
    spec, dataset = make_approved_spec()  # single_data_indicator,含参数界
    plan = sweep.prepare_sweep(state, "session-001", spec, dataset,
                               {"fast_period": [10, 20], "slow_period": [30, 40]})
    assert len(plan["cells"]) == 4
    hashes = {c["cell_hash"] for c in plan["cells"]}
    assert len(hashes) == 4  # 确定性且互异


def test_sweep_prepare_rejects_out_of_bounds(tmp_path, make_approved_spec):
    spec, dataset = make_approved_spec()
    with pytest.raises(AgentError) as exc:
        sweep.prepare_sweep(state, "session-001", spec, dataset,
                            {"fast_period": [999999]})  # 超出 minimum/maximum
    assert exc.value.code == "BTAG-SWEEP-BOUNDS"


def test_sweep_plan_is_immutable_on_disk(tmp_path, make_approved_spec):
    spec, dataset = make_approved_spec()
    plan = sweep.prepare_sweep(...)
    path = state / "sweeps" / plan["sweep_id"] / "sweep-plan.json"
    # 篡改后重新加载必须失败(plan 内嵌 plan_hash 自校验)
    payload = json.loads(path.read_text())
    payload["cells"][0]["params"]["fast_period"] = 999
    path.write_text(json.dumps(payload))
    with pytest.raises(AgentError):
        sweep.load_plan(state, plan["sweep_id"])
```

- [ ] **Step 2: 运行确认失败** — `sweep` 模块不存在。
- [ ] **Step 3: 实现** — `prepare_sweep`:网格笛卡尔积展开;每 cell 参数值必须落在 spec `parameters[name]["minimum"/"maximum"]` 界内(越界 → `BTAG-SWEEP-BOUNDS`);`cell_hash = hash_object({"spec_hash", "params"})`;plan 落盘含 `plan_hash = hash_object(plan 除 plan_hash 外字段)`;`load_plan` 校验 `plan_hash` 不符抛 `BTAG-SWEEP-PLAN`。CLI:`sweep prepare --session-id --spec --dataset-manifest --param-grid`(参数网格用 Task 3 的 `_json_load`,dict 校验;值列表须非空且数值)。会话 journal 记录 `sweep-prepare` 事件(action 类型 `sweep`)。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: 提交** `feat: add immutable SweepPlan with deterministic parameter grid expansion`

### Task 14:sweep 审批 kind(R16)

**Files:**
- Modify: `src/backtrader_agent/cli.py:182`(`--kind` choices 加 `"sweep"`)
- Modify: `src/backtrader_agent/tokens.py:24-60`(`REQUIRED_BINDINGS` 加 `"sweep"` 条目)、`tokens.py:335-355`(`_validate_bindings` 适配)
- Test: `tests/test_sweep.py` 追加

**Interfaces:**
- Consumes: Task 13 的 `prepare_sweep`/`load_plan`
- Produces: `approval request --kind sweep --subject-hash <plan_hash> --bindings {...}` 走通;bindings 必含 `session_id, sweep_plan_hash, dataset_manifest_hash, environment_hash, engine_hash, engine_root_id, spec_hash`

- [ ] **Step 1: 写失败测试**

```python
def test_sweep_approval_roundtrip(tmp_path, make_sweep_plan):
    state, plan = make_sweep_plan()
    request = authority.prepare_approval("sweep", plan["plan_hash"], {绑定 dict})
    grant = authority.grant_approval(request["request_id"], approver="me", confirmed=True)
    assert grant["token"]["kind"] == "sweep"


def test_sweep_token_replay_rejected(tmp_path, make_sweep_plan):
    # 同一 sweep token 二次消费 → BTAG-TOKEN-*;跨会话复用 → 拒绝
    ...


def test_sweep_kind_missing_bindings_rejected(tmp_path, make_sweep_plan):
    with pytest.raises(AgentError) as exc:
        authority.prepare_approval("sweep", plan["plan_hash"], {"session_id": "x"})
    assert exc.value.code == "BTAG-TOKEN-BINDINGS"  # 以现有绑定校验错误码为准
```

- [ ] **Step 2: 运行确认失败** — `--kind sweep` 被 argparse 拒绝(choices)。
- [ ] **Step 3: 实现** — 两处枚举同步;`verify()` 对 sweep kind 走 `expected_bindings("sweep", ...)`(Task 7 的助手扩展到 sweep);token 一次性语义复用既有逻辑,无需新机制。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: 提交** `feat: add one-time sweep approval token kind`

### Task 15:sweep run/report(R17/R18)

**Files:**
- Modify: `src/backtrader_agent/sweep.py`、`src/backtrader_agent/runner/execute.py`(抽 `_execute_profile`)
- Modify: `src/backtrader_agent/cli.py`(`sweep run`/`sweep report` 接线)
- Test: `tests/test_sweep.py` 追加 + `scripts/run_acceptance.py` 加 sweep 冒烟

**Interfaces:**
- Consumes: Task 13/14 产物;`ControlledRunner` 的执行核心(拆分出的 `_execute_profile`)
- Produces:
  - `sweep.run_sweep(state, roots, authority, sweep_id, token, max_cells=100, timeout_per_cell=120) -> Dict[str, Any]` — 逐 cell:渲染私有草稿(`<state>/sweeps/<sweep_id>/cells/<cell_hash>/`)→ 复用 `StrategyValidator`(approval="validator")→ 经 `_execute_profile` 执行 → cell 级 RunManifest/RunResult(落 cell 目录);cell 瞬态失败按 Task 12 白名单重试一次
  - `sweep.sweep_report(state, sweep_id) -> Dict[str, Any]`(`sweep-result-v1`:逐 cell 指标/参数/run id,按 `final_value` 降序)

- [ ] **Step 1: 写失败测试**

```python
def test_sweep_run_two_by_two(tmp_path, make_approved_sweep):
    state, sweep_id, token = make_approved_sweep(grid={"fast_period": [5, 10], "slow_period": [15, 20]})
    result = sweep.run_sweep(state, roots, authority, sweep_id, token)
    assert result["cells_completed"] == 4
    report = sweep.sweep_report(state, sweep_id)
    assert report["schema_version"] == "sweep-result-v1"
    finals = [c["metrics"]["final_value"] for c in report["cells"]]
    assert finals == sorted(finals, reverse=True)  # 排名


def test_sweep_run_respects_max_cells(tmp_path, make_approved_sweep):
    result = sweep.run_sweep(..., max_cells=2)
    assert result["cells_completed"] == 2
    assert result["cells_skipped"] == 2


def test_sweep_cell_draft_stays_private(tmp_path, make_approved_sweep):
    # sweep 结束后,workspace 目录没有任何新文件;草稿只在 state/sweeps/ 下
    ...
```

- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现** — 拆分 `_execute_profile(profile: Dict[str, Any]) -> subprocess.CompletedProcess` 自 `ControlledRunner.run()`(纯移动);`run_from_owned_draft` 路径:校验 sweep token → 逐 cell 渲染/验证/执行 → 结果落盘;report 汇总排名。sweep 完成后会话 journal 记录 `sweep-complete` 事件。
- [ ] **Step 4: 运行确认通过**;`run_acceptance.py` 加 2×2 sweep 冒烟门(clean-wheel)。
- [ ] **Step 5: 提交** `feat: execute approved sweep cells through the controlled runner`

### Phase 1 门

对照 acceptance.md A1-1 至 A1-8 回填证据;发行门全绿(含新 eval job)。

---

## Phase 2:工程轨

### Task 16:宿主调用追踪(R19)

**Files:**
- Create: `src/backtrader_agent/observability.py`
- Modify: `src/backtrader_agent/cli.py:519-548`(`main` 记录每笔调用)
- Test: `tests/test_observability.py`(新)

**Interfaces:**
- Produces: `observability.record_call(state: Path, session_id: Optional[str], command: str, arg_hashes: Dict[str, str], duration_ms: int, exit_code: int, error_code: Optional[str]) -> None`;写 `<state>/trace/<session-id>.jsonl` 或 `<state>/trace/global.jsonl`(app 式、持 lock、不含 secret)

- [ ] **Step 1: 写失败测试**

```python
def test_success_and_failure_calls_are_traced(tmp_path):
    state = tmp_path / "state"
    cli.main(["--state-root", str(state), "doctor", "--json"])          # 成功
    cli.main(["--state-root", str(state), "report", "--run-id", "bad"])  # 失败
    lines = [json.loads(l) for l in (state / "trace" / "global.jsonl").read_text().splitlines()]
    assert {l["command"] for l in lines} == {"doctor", "report"}
    assert any(l["exit_code"] == 0 for l in lines)
    assert any(l["exit_code"] == 3 and l["error_code"] for l in lines)


def test_trace_has_session_context(tmp_path):
    state = tmp_path / "state"
    SessionStore(state).create("session-001")
    cli.main(["--state-root", str(state), "session", "status", "--session-id", "session-001"])
    lines = [json.loads(l) for l in (state / "trace" / "session-001.jsonl").read_text().splitlines()]
    assert lines[-1]["session_id"] == "session-001"
    assert "duration_ms" in lines[-1]


def test_trace_contains_no_secrets(tmp_path):
    state = tmp_path / "state"
    cli.main(["--state-root", str(state), "approval", "grant",
              "--request-id", "req-x", "--approver", "secret-approver", "--confirm"])
    blob = (state / "trace" / "global.jsonl").read_text()
    assert "secret-approver" not in blob  # 只记参数 hash
```

- [ ] **Step 2: 运行确认失败** — trace 目录不存在。
- [ ] **Step 3: 实现** — `main()`:起始 `time.monotonic()`;`session_id = getattr(args, "session_id", None)`;arg_hashes = 各已知参数的 `sha256_bytes(str(v))`(approver 等敏感参数只记 hash);finally 风格记录(成功与异常分支都调 `record_call`)。文件 append 用既有 locking 模块的稳定锁。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: 提交** `feat: trace every CLI invocation with hashed arguments into the state root`

### Task 17:子进程输出保留(R20)

**Files:**
- Modify: `src/backtrader_agent/runner/execute.py`(Task 15 拆出的执行核心;Task 7 拆分后原 runner.py 内 `_run_locked` 逻辑)
- Test: `tests/test_observability.py` 追加

**Interfaces:**
- Produces: 每次受控 run 的 run 目录含 `stdout.log`/`stderr.log`(成功路径;截断至现有输出配额;失败路径保持脱敏尾部语义)

- [ ] **Step 1: 写失败测试**

```python
def test_successful_run_retains_child_outputs(tmp_path, make_approved_run_env):
    run_id = make_approved_run_env()  # 走完 approve+run
    run_dir = tmp_path / "state" / "runs" / run_id
    assert (run_dir / "stdout.log").is_file()
    assert (run_dir / "stderr.log").is_file()
```

- [ ] **Step 2: 运行确认失败** — 成功路径无 stderr 文件。
- [ ] **Step 3: 实现** — 执行核心在 `proc.communicate()` 后把 stdout(剔除 `BACKTRADER_AGENT_RESULT=` 行)与 stderr 各写 `stdout.log`/`stderr.log`(truncate 到配额上限,超限写尾部 + 截断标记行);失败路径在既有脱敏逻辑后同样落文件。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: 提交** `feat: persist child stdout/stderr for every controlled run`

### Task 18:doctor 状态审计(R21)

**Files:**
- Modify: `src/backtrader_agent/doctor.py:84-176`、`src/backtrader_agent/cli.py:110-111`(`--audit` 参数)
- Test: `tests/test_observability.py` 追加

**Interfaces:**
- Produces: `doctor.audit_state(state: Path) -> List[Dict[str, Any]]`,每项 `{code, severity, message, hint}`;检查:损坏 journal(session recover 只读验证)、`RUNNING` 孤儿(超阈值时长)、CAS hash 违规、过期审批堆积、trace/memory 目录健康。

- [ ] **Step 1: 写失败测试**

```python
def test_doctor_audit_reports_corrupt_journal(tmp_path):
    state = tmp_path / "state"
    s = SessionStore(state); s.create("session-001")
    j = state / "sessions" / "session-001" / "journal.jsonl"   # 路径以源码为准
    j.write_text(j.read_text() + '{"garbage": true}\n')
    diags = doctor.audit_state(state)
    assert any(d["code"] == "BTAG-AUDIT-JOURNAL" for d in diags)


def test_doctor_audit_reports_running_orphan(tmp_path):
    # 手工构造 RUNNING 会话 manifest(时间戳回拨) → BTAG-AUDIT-ORPHAN
    ...


def test_doctor_audit_clean_state_is_empty(tmp_path):
    assert doctor.audit_state(tmp_path / "state") == []
```

- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现** — `doctor --state-root <root> --audit`:复用 `SessionStore` 的只读验证(不修改);orphan 阈值用常量 `ORPHAN_RUNNING_SECONDS = 3600`;CAS 扫描 `data/sha256/*/*` 抽样或全量(加 `--audit-deep` 才全量,默认检查计数与 manifest 引用一致性);CLI 输出为 envelope 内 `diagnostics` 列表。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: 提交** `feat: add state-root health audit to doctor`

### Task 19:跨会话记忆(R22)

**Files:**
- Create: `src/backtrader_agent/memory.py`
- Modify: `src/backtrader_agent/sweep.py`(sweep 完成时写参数先验)、`src/backtrader_agent/cli.py`(`memory` 子命令组:list/set-note)
- Test: `tests/test_memory_store.py`(新)

**Interfaces:**
- Produces:
  - `memory.MemoryStore(state: Path)`,方法 `datasets() -> Dict[str, Any]`、`note_dataset(dataset_id, note) -> None`、`param_priors(archetype: str) -> List[Dict[str, Any]]`、`record_priors(archetype, cells) -> None`
  - 文件:`<state>/memory/datasets.json`、`<state>/memory/params.json`(原子写、schema 校验、`hash_object` 自校验)

- [ ] **Step 1: 写失败测试**

```python
def test_memory_store_roundtrip_and_tamper(tmp_path):
    store = memory.MemoryStore(tmp_path / "state")
    store.note_dataset("ds_x", "daily bars, works well with sma")
    assert store.datasets()["ds_x"]["note"] == "daily bars, works well with sma"
    # 篡改后加载拒绝
    p = tmp_path / "state" / "memory" / "datasets.json"
    payload = json.loads(p.read_text()); payload["ds_x"]["note"] = "hacked"
    p.write_text(json.dumps(payload))
    with pytest.raises(AgentError):
        store.datasets()


def test_sweep_writes_param_priors(tmp_path, make_approved_sweep):
    state, sweep_id, token = make_approved_sweep(...)
    sweep.run_sweep(state, roots, authority, sweep_id, token)
    priors = memory.MemoryStore(state).param_priors("single_data_indicator")
    assert priors and "fast_period" in priors[0]["params"]
```

- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现** — `MemoryStore` 用既有原子写纪律 + `hash_object` 自校验;`sweep.run_sweep` 完成时按 cell 排名写入参数先验(每 archetype 保留 top 5);`memory list --datasets`/`memory note --dataset-id --note` CLI;payload 的复用指引已在 Task 8 落地,此处接 `data list` 复用测试。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: 提交** `feat: add cross-session memory store for dataset notes and parameter priors`

---

## Phase 2:功能轨

### Task 20:扩展指标(R23)

**Files:**
- Modify: `src/backtrader_agent/runner/`(runner 模板 analyzers 注册)、`src/backtrader_agent/report.py`(指标采集)、`src/backtrader_agent/resources/contracts/run-result-v1.schema.json`(可选 `extended_metrics` + `$defs`)
- Test: `tests/test_runner_installer_audit.py` 追加 + schema 测试

**Interfaces:**
- Produces: RunResult 新增可选 `extended_metrics`: `{trade_analyzer: {profit_factor, avg_holding_bars, max_consecutive_wins, max_consecutive_losses}, sqn, calmar, vwr, gross_leverage, positions_value}`,任何子项可为 null;11 标量保持 required。

- [ ] **Step 1: 写失败测试**

```python
REQUIRED_SCALARS = {"bar_num", "buy_count", "sell_count", "win_count", "loss_count",
                    "trade_num", "final_value", "sharpe_ratio", "annual_return",
                    "max_drawdown", "return_rate"}


def test_run_result_extended_metrics_schema(tmp_path, make_approved_run_env):
    result = _load_run_result(make_approved_run_env())  # 真实 cell 运行
    assert "extended_metrics" in result
    em = result["extended_metrics"]
    assert "sqn" in em and "calmar" in em
    assert REQUIRED_SCALARS <= set(result["metrics"])  # 既有 required 不变
    jsonschema.validate(result, RUN_RESULT_SCHEMA)  # 打包 schema 校验通过


def test_eleven_scalars_still_required_in_schema():
    schema = json.loads(...run-result-v1.schema.json...)
    assert set(schema["required"]) == {"bar_num", "buy_count", "sell_count", "win_count",
                                       "loss_count", "trade_num", "final_value",
                                       "sharpe_ratio", "annual_return", "max_drawdown",
                                       "return_rate"}
```

- [ ] **Step 2: 运行确认失败** — 无 extended_metrics。
- [ ] **Step 3: 实现** — runner 模板在固定装配路径 `cerebro.addanalyzer` 注册 TradeAnalyzer/SQN/Calmar/VWR/GrossLeverage/PositionsValue(经 `bt.analyzers` 固定导入名,不开放任意 analyzer);`report.py` 归一化采集(非有限值置 null 并记 warning);schema `$defs` 版本化扩展;分析器缺失/异常时 `extended_metrics` 为 null 不失败。
- [ ] **Step 4: 运行确认通过**;`run_acceptance.py` 全绿。
- [ ] **Step 5: 提交** `feat: collect extended analyzer metrics into RunResult`

### Task 21:Sizers(R24)

**Files:**
- Modify: `src/backtrader_agent/contracts.py`(`sizing` 区块校验:`{method: fixed|percent, fixed_size|percent}`,缺省 null)、`src/backtrader_agent/scaffold.py`(渲染段)、`src/backtrader_agent/validator.py`(白名单:FixedSize/PercentSizer + 受限参数)
- Test: `tests/test_sweep.py`? 不 — `tests/test_scaffold_validator_catalog.py` 追加 + 真实 cell

- [ ] **Step 1: 写失败测试**

```python
def test_spec_rejects_invalid_sizing():
    spec = valid_spec()
    spec["sizing"] = {"method": "martingale"}
    with pytest.raises(AgentError):
        StrategySpec.from_dict(spec)


def test_scaffold_renders_sizer(tmp_path, make_env):
    spec = valid_spec(sizing={"method": "fixed", "fixed_size": 100})
    source = render_for(spec)  # 渲染产物源码
    assert "bt.sizers.FixedSize" in source and "100" in source
    # validator 对未白名单 sizer 类拒绝
    bad = source.replace("FixedSize", "CustomSizer")
    with pytest.raises(AgentError) as exc:
        StrategyValidator(authority).validate_source(bad)
    assert exc.value.code == "BTAG-VALIDATE-IMPORT"  # 以现有 import 白名单错误码为准
```

- [ ] **Step 2: 运行确认失败** — sizing 不生效。
- [ ] **Step 3: 实现** — contracts 校验 `sizing` 结构(非法 method/越界值 → `BTAG-SPEC-SIZING`);scaffold 每个 archetype 模板尾部加 sizing 渲染段(`cerebro.addsizer(bt.sizers.FixedSize(stake=100))` 或 `PercentSizer(percents=95)`);validator 允许 `bt.sizers.FixedSize`/`bt.sizers.PercentSizer` 的受限构造;README 诚实边界段落同步更新(entry/exit/risk 仍不翻译)。
- [ ] **Step 4: 运行确认通过**;真实 cell 运行验证持仓变化。
- [ ] **Step 5: 提交** `feat: render fixed and percent sizers from the StrategySpec sizing block`

### Phase 2 门

对照 acceptance.md A2-1 至 A2-6 回填;发行门全绿。

---

## Phase 3:收尾

### Task 22:指标注册表(R25)

**Files:**
- Create: `scripts/extract_indicator_registry.py`
- Create: `src/backtrader_agent/resources/catalog/indicator-registry-v1.json`
- Modify: `src/backtrader_agent/catalog.py`(`--kind indicator`)、`src/backtrader_agent/cli.py`(search 参数)
- Test: `tests/test_catalog_snapshot.py` 追加

- [ ] **Step 1: 写失败测试**

```python
def test_indicator_registry_packaged_and_searchable():
    data = json.loads(pkg_resources.files("backtrader_agent.resources")
                      .joinpath("catalog/indicator-registry-v1.json").read_text("utf-8"))
    assert data["schema_version"] == "indicator-registry-v1"
    assert all(e["source_available"] is False for e in data["indicators"])
    assert any(e["class_name"] == "Sma" or e["module"].endswith("sma") for e in data["indicators"])
    hits = catalog.SnapshotCatalog().search_indicators("bollinger", top_k=3)
    assert hits and all("bollinger" in h["class_name"].lower() for h in hits)
```

- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现** — 提取脚本:只读扫描 `BACKTRADER_AGENT_INDICATOR_ROOT`(缺省取已注册 engine root 或环境变量,无则 skip 并说明)下 `backtrader/indicators/*.py`,AST 提取 module/class/`params` 元组字段名,生成 JSON(纯元数据);打包进 wheel;`catalog search --kind indicator` 词法检索(复用现有 tokenize)。
- [ ] **Step 4: 运行确认通过**;manifest 重生成。
- [ ] **Step 5: 提交** `feat: add packaged indicator registry extracted from the engine source`

### Task 23:Timers/cheat(R26)

**Files:**
- Modify: `src/backtrader_agent/contracts.py`(可选 `timers`/`cheat` 区块,默认关)、`src/backtrader_agent/validator.py`(白名单:Timer、cheat_on_open/cheat_on_close、broker_coo)、`src/backtrader_agent/scaffold.py`(`multi_timeframe` 等模板渲染段)
- Test: `tests/test_scaffold_validator_catalog.py` 追加 + 真实 cell

- [ ] **Step 1: 写失败测试**

```python
def test_spec_accepts_timer_block_and_defaults_off():
    on = StrategySpec.from_dict(valid_spec(timers=[{"when": "session", "callback": "notify_timer"}]))
    assert on.to_dict()["timers"] == [...]
    off = StrategySpec.from_dict(valid_spec())
    assert off.to_dict().get("timers") is None


def test_validator_rejects_unapproved_timer_usage():
    source = "class S(bt.Strategy):\n    def __init__(self): self.t = bt.Timer(...)\n"
    # 白名单内通过、白名单外 API(如任意线程库)拒绝
    ...


def test_scaffold_renders_timer_segment(tmp_path, make_env):
    source = render_for(valid_spec(archetype="multi_timeframe",
                                   timers=[{"when": "cheat", "callback": "check_rebalance"}]))
    assert "add_timer" in source and "cheat_on_close" in source
```

- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现** — contracts:`timers: List[{when: session|cheat|both, callback: 白名单函数名}]`、`cheat: {on_open|on_close: bool}` 可选区块;validator 白名单 `bt.Timer`、`add_timer`、`cheat_on_open/cheat_on_close`、`broker_coo`;scaffold 对应模板段;非法 callback 名/未知 when → `BTAG-SPEC-TIMERS`。
- [ ] **Step 4: 运行确认通过**;真实 cell 运行。
- [ ] **Step 5: 提交** `feat: support timers and cheat modes in validated scaffolds`

### Task 24:发行收尾

**Files:**
- Modify: `README.md`、`CHANGELOG.md`、`manifest.json`(重生成)、`docs/iterations/final-convergence-audit.md`(停止条件声明更新)、`docs/iterations/iteration-013-*/README.md`(验收结论回填)

- [ ] **Step 1: 全量发行门** — 三解释器 pytest、ruff、black、`audit_independence.py` 6/6、`doctor` ready、`run_acceptance.py`(含 sweep 冒烟)全绿、`scripts/run_evals.py` 全绿、分发契约。
- [ ] **Step 2: 文档同步** — README:P0 工作流加 sweep/重试/新 envelope;诚实边界更新(sizing 落地、entry/exit/risk 仍不翻译);CHANGELOG 0.2.0 条目;`build_manifest.py` 重生成。
- [ ] **Step 3: 回填验收文档** — acceptance.md 全部 `[ ]` → `[x]` 并附证据;README「验收结论」。
- [ ] **Step 4: 提交** `docs: complete iteration-013 release notes and acceptance evidence`

---

## 依赖与并行性

```text
Task 1-3 → Task 4-7(Phase 0 顺序内可微调;1-3 先行)
Phase 0 全部 → Phase 1 两条轨
Task 8 → Task 9/10(harness 以 payload 为 spec)
Task 12 → Task 15(cell 重试复用瞬态白名单)
Task 16 依赖 Task 1/2(envelope 与 exit code 稳定)
Task 20/21 依赖 Task 5(注册表就绪后模板可扩展)
Task 13-15 依赖 Task 7(_execute_profile 拆分)与 Task 3(_json_load)
```

并行执行注意:同轨任务互不共享文件时可用 subagent 并行;共享文件(cli.py、sessions.py、tokens.py)的任务必须串行,避免合并冲突。

## 测试与验证纪律

- 每个任务先 RED 后 GREEN,提交信息按 `<type>: <description>`;每任务结束时跑相关测试 + `ruff check src tests scripts` + `black --check`。
- 任何安全模型改动必须附 red test(伪造/重放/越界/越权路径)。
- 每阶段结束跑全套发行门并回填 acceptance.md。
