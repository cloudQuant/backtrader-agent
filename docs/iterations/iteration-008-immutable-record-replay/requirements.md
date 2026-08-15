# 迭代 008：Immutable Record 并发重放需求文档

## 1. 目标

在不放宽 no-clobber 安全语义的前提下，让同一 immutable product record、dataset、draft artifact 或
adapter install 的并发相同请求线性化为“一个创建、其余验证并重放”。

## 2. 功能需求

### R1：提供 canonical exact-create-or-verify helper

- helper 对预期 bytes 首先执行真实 `create_only` publish；若目标已存在或竞态得到
  `BTAG-WRITE-EXISTS`，必须重新读取并要求它是非 symlink 的普通文件且 bytes 完全相等。
- 返回值须区分本 caller 是否实际创建，供 installer 正确报告 `create`/`unchanged`；不同 bytes、
  symlink、目录、读取失败或非 create-only 写入失败必须映射为调用方提供的 conflict code，绝不
  replace/覆盖。
- JSON 变体必须以 canonical JSON bytes（含换行）比较；`atomic_write_*` 的公开语义不变。

### R2：迁移四类用户可见 immutable 调用者

- `DatasetService.register()` 的 CAS object 与 dataset manifest：两个同 spec worker 都成功，最终
  CAS digest 与 manifest 精确；不同内容继续是 `BTAG-CAS-COLLISION` 或 `BTAG-DATASET-CONFLICT`。
- `ArtifactRenderer.render()` 的 draft file、artifact manifest、provenance record：两个同 revision
  worker 都成功且 artifact hash/record hash 相同；不同 bytes 仍保留 `BTAG-DRAFT-*`/
  `BTAG-PROVENANCE-CONFLICT`。
- `TokenAuthority.store_bound_record()`：相同 signed record 的两个 caller 都成功；不同 record 保持
  `BTAG-RECORD-CONFLICT`。
- `AdapterInstaller.install(..., apply=True)`：同 host/target 的两个 worker 都成功，最终所有文件和
  install manifest 精确，实际创建者为 `installed`、竞争重放者为 `unchanged`；不同 target bytes
  保持 `BTAG-INSTALL-*`。

### R3：兼容与安全

- 使用第 006 轮的 atomic no-replace 基础，不增加依赖，兼容 Python 3.8/Windows/POSIX。
- 现有 sequential repeat、preview、uninstall、CAS read/hash、artifact signature 与 run contracts
  保持不变；MIT 许可证不变。
- 真实 spawn 测试必须在 `os.link` publish 边界同步，不能仅测试已存在文件的顺序重试。

## 3. 非功能约束

- 仅修改 canonical helper、上述四个调用者、测试、迭代文档和派生 manifest。
- 所有 Python 验收使用用户 Anaconda 环境，并禁用第三方 pytest 自动加载。

## 4. 需求追踪

| 需求 | 主验收证据 |
| --- | --- |
| R1 | bytes/JSON exact helper 的 create、same race、mismatch/symlink 错误映射 |
| R2 | dataset、artifact、bound record、installer 四组 spawn workers 与最终对象断言 |
| R3 | 现有 data/scaffold/token/installer 回归、三解释器与 clean-wheel |
