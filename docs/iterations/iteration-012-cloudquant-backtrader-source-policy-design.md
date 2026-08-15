# 迭代 012：CloudQuant Backtrader 来源策略——设计

**日期：** 2026-08-02  
**状态：** 已实现

## 设计概览

新增 `backtrader_runtime` 模块，负责当前解释器中 `backtrader` 的发现、来源证明与按需
安装。该模块不导入用户策略，也不在普通 import 阶段执行 pip。

```text
pyproject extras ──direct VCS requirement──> pip installs cloudQuant/backtrader
                                                │
CLI check / doctor ──> inspect_backtrader_runtime │
                                                ▼
                                    direct_url.json / Git origin
                                                │
                          missing ──> ensure ──> current Python -m pip install
                          mismatch ──> warning (no replacement)
```

## 来源判定

1. 用 `importlib.util.find_spec("backtrader")` 判断可导入性，并记录解析后的模块路径。
2. 读取 `importlib.metadata.distribution("backtrader")` 的版本和 `direct_url.json`。
3. HTTPS/SSH VCS URL 归一化后与
   `https://github.com/cloudQuant/backtrader` 比较；本地 `file://` direct URL 或模块路径
   则向上定位 Git 工作树并读取 `origin` remote。
4. 只有 direct URL 或 Git remote 可证明目标仓库时才标记为 `verified`。包主页、版本号及
   名称可作为诊断信息，不能成为来源证明。

## 安装与警告边界

- `ensure_cloudquant_backtrader()` 仅当模块缺失时，使用运行中解释器的
  `sys.executable -m pip install "backtrader @ git+https://github.com/cloudQuant/backtrader.git"`。
  安装后失效 import cache 并重新检查。
- 若模块已经存在，无论来源是否匹配均不调用 pip；非匹配/不可验证结果含 warning。
- `backtrader-agent backtrader check|ensure` 返回结构化 JSON；主 CLI 把 warning 同时输出到
  stderr。`doctor` 只检查并在 JSON 的 `warnings` 及 `environment.backtrader` 中报告。
- `inspect_engine()` 把注册 engine root 的 Git 来源加入其哈希绑定 descriptor；
  `ControlledRunner` 在其原有依赖预检中调用 ensure，并对当前解释器或 engine root 的
  不匹配来源经 `warnings.warn` 发出警告。它不会改变已注册的 engine root 内容。

## 兼容性与安全性

- 代码使用 Python 3.8 兼容的 `typing` 注解。
- 所有 pip 调用使用固定 argv、`shell=False`、超时和有限的失败输出；不存在用户提供的
  安装 URL 或命令拼接。
- 现有 engine root 内容哈希与验证 token 绑定保持不变；本迭代只约束该项目所依赖的
  Backtrader 分发来源和用户可见诊断。
