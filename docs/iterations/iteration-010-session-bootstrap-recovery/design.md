# 迭代 010：会话创建引导恢复设计文档

## 1. Bootstrap 状态机

`create()` 已在稳定的 per-session advisory lock 内调用 `_create_unlocked()`。在 lock 内将引导状态划分为：

```text
manifest exists                 -> 验证/返回既有 session（原语义）
manifest absent + journal absent -> create empty journal, publish NEW manifest
manifest absent + regular empty journal -> 复用 journal, publish NEW manifest
manifest absent + other journal  -> BTAG-SESSION-BOOTSTRAP, 不写入
```

第二种与第三种路径都只执行 manifest 的原子发布。journal 在此前已由 `atomic_write_bytes(...,
create_only=True)` 以 fsync 写入，因此第三种状态恰是 manifest publish 之前进程死亡留下的唯一安全残留。

## 2. 完整性与拒绝原则

复用前用 `Path.is_symlink()`、`Path.is_file()` 和 `read_bytes() == b""` 验证 journal。任何读失败、符号链接、
目录、设备或非空内容统一映射 `BTAG-SESSION-BOOTSTRAP`，并且不会调用覆盖写入。这样不会把可能已经包含
有效事件或攻击者控制路径的状态伪装为 NEW session。

`manifest.json` 优先级高于 journal bootstrap 检查：已有 manifest 继续走既有 idempotent / conflict 分支。
这避免改变正常完成创建后的行为。

## 3. 线性化和测试设计

测试 worker 使用 `multiprocessing.get_context("spawn")`，并在实际 `manifest.json` 的
`atomic_write_json` 调用点执行 `os._exit()`。OS 会释放该进程持有的 advisory lock，而已经 fsync 的空
journal 保留。父进程重新 `create()` 必须产出完整 NEW manifest。

另有单进程残留形状测试：人工写入非空普通 journal 或 symlink journal，断言稳定诊断且原对象/字节未变。
既有跨进程 create、transition、recover 测试继续证明该最小恢复不削弱第 002 轮的线性化不变量。
