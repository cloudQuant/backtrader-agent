# 迭代 012：CloudQuant Backtrader 来源策略——验收

**日期：** 2026-08-02  
**状态：** 通过

## 验收矩阵

| 门 | 验证内容 | 预期 | 结果 |
| --- | --- | --- | --- |
| 来源识别 | CloudQuant HTTPS direct URL | `verified`，无 warning | 通过：单元测试覆盖 |
| 来源识别 | 非 CloudQuant VCS URL/不可验证安装 | 保留安装，不覆盖，返回 warning | 通过：单元测试覆盖 |
| 缺包补齐 | `ensure` 的模拟安装路径 | 固定 `sys.executable -m pip`，安装后复检 | 通过：单元测试覆盖 |
| CLI/doctor | check、ensure 与 doctor 机器可读诊断 | 含 `environment.backtrader` 和可见 warning | 通过：单元测试覆盖 |
| 打包契约 | wheel `backtest`、`test` extras | 直接指向 CloudQuant Git VCS URL | 通过：base、Py3.8、Py3.12 |
| 回归 | 相关 pytest、静态独立性审计、受控验收 | 全部通过 | 通过 |

## 当前环境检查

验收时需记录当前解释器的结果；若已安装且其 `direct_url.json` 指向本地 CloudQuant
工作树，则应识别为 verified，而不重新安装。若来源不同，预期仅出现警告，不改变该环境。

**实际结果：** base 环境的 `backtrader` 为 1.2.0，`direct_url.json` 指向本机
CloudQuant 工作树，Git `origin` 归一化为
`https://github.com/cloudQuant/backtrader`；`backtrader-agent backtrader check` 返回
`status=verified`、`warning=null`，故没有触发安装。

## 实际命令与证据

- `pytest tests -q -p no:cacheprovider`：**136 通过**，只有既有 Backtrader Quandl
  弃用 warning。
- Python 3.8 与 Python 3.12：来源策略与 wheel 契约的 **9 项**测试均通过；Py3.8
  验证了 direct-reference 元数据的无空格序列化。
- `ruff check src/backtrader_agent tests scripts`：通过。
- `python scripts/audit_independence.py`：6/6 通过，产品快照 hash
  `08da3d131e2d85bb73c1888e7b25e8f81bc8d8d482e80fc9dd2a48cd562c73fa`。
- `python scripts/run_acceptance.py`：clean wheel build/install/probe 通过；7 个
  archetype × 2 profiles 共 **14/14** 单元、crash-resume 和 repair gate 通过（矩阵
  `14 passed, 1 warning in 152.28s`）。

## 完成定义

所有门均获得实际命令输出或对应测试证据后，将本文件更新为“通过”，并记录命令、通过数、
环境来源结论及任何已知限制。
