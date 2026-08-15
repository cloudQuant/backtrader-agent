# 迭代 012：CloudQuant Backtrader 来源策略——需求

**日期：** 2026-08-02  
**状态：** 已完成

## 目标

将 `backtrader-agent` 的受控 Backtrader 依赖限定为
[`cloudQuant/backtrader`](https://github.com/cloudQuant/backtrader)，并让用户能够在当前
Python 解释器中明确检查、补齐和诊断该依赖。

## 范围

1. `backtest` 与 `test` extra 必须以 CloudQuant GitHub 仓库的直接 VCS 依赖声明
   `backtrader`，不能继续接受泛化的 PyPI 版本范围。
2. 新增可复用的运行时来源检查：至少使用 pip 的 `direct_url.json` 或本地 Git
   `origin` remote 作为可验证证据；`Home-page` 元数据不得单独作为通过依据。
3. 提供 CLI 检查与 ensure 操作：
   - 未安装 `backtrader` 时，`ensure` 在当前 Python 解释器中安装 CloudQuant 仓库；
   - 已安装且可验证为 CloudQuant 来源时，通过；
   - 已安装但来源不匹配或无法验证时，输出明确警告，且不得静默替换用户已有安装。
4. `doctor --json` 必须把来源状态和警告包含在机器可读结果中；受控运行在发现
   非 CloudQuant/不可验证安装时也必须发出运行时警告。
5. 文档须说明安装命令、来源证据规则以及“不自动替换已有不匹配安装”的边界。

## 非目标与约束

- 不修改 MIT 许可证，不创建 Git 分支，不提交或推送。
- 不自动替换已有但来源不匹配的 `backtrader`；用户要求的是警告而不是覆写。
- 不把网络安装隐藏在只读的 `doctor` 检查中；安装仅由显式 `ensure` 或缺失依赖的
  受控执行预检触发。
- 不以 Backtrader 的网页主页字段、版本号或包名本身推断 Git 来源。

## 验收条件

1. wheel 元数据中，`backtest` 和 `test` extra 的 `backtrader` 要求精确指向
   `git+https://github.com/cloudQuant/backtrader.git`。
2. 对 CloudQuant VCS URL 的检查结果为 verified；外部 VCS URL 与缺少可验证来源的
   安装结果均包含 warning。
3. 缺包路径仅调用当前解释器的 `-m pip install`，安装成功后再次检查；安装失败提供
   不泄露路径/环境的结构化诊断。
4. CLI/doctor 与受控 runner 的输出能让用户看到上述 warning；已有不匹配包不会被
   `ensure` 覆盖。
5. 相关单元、分发契约、静态审计和受控验收均通过。

## 实现结论

- `backtest` 和 `test` extra 已改为 CloudQuant Git 直接依赖；Python 3.8 的 wheel
  metadata 使用无空格 `backtrader@` 序列化也被契约测试覆盖。
- 当前解释器、注册 engine root、doctor 与 controlled runner 均会报告来源证据；可验证
  来源通过，其他已安装来源仅输出 warning。
- 只有缺包时才由 `ensure` 使用当前解释器调用固定的 pip argv 安装；已有非匹配安装
  从不被替换。
