# 迭代 011：Adapter manifest 与链接完整性需求文档

## 1. 目标

使 `AdapterInstaller.uninstall(..., apply=True)` 只会删除当前 host 的受控 adapter 路径中的常规、
manifest-hash 匹配文件，绝不把 target 内可修改 manifest 解释为可越界的删除清单。

## 2. 功能需求

### R1：受控 manifest 语义

- installer manifest 必须为常规、非 symlink 文件，且 `schema_version`、`host`、`files` 结构合法。
- `files` 的 relative path 集合必须恰为当前 host 的允许 adapter path 集合；不得含重复项、绝对路径、
  `.`/`..`、未知路径或非法 SHA-256 字符串。
- 任何不满足条件的 manifest 返回稳定 `BTAG-UNINSTALL-MANIFEST`，不删除 adapter、manifest 或 target 外
  文件。

### R2：adapter 文件身份与 preview 一致性

- install preview/apply 均把已存在 symlink、目录或不同字节的 adapter path 视为
  `BTAG-INSTALL-CONFLICT`；不得在 preview 中误报 `unchanged`。
- uninstall 仅对常规、非 symlink 且 SHA-256 与已验证 manifest 一致的文件执行 unlink。缺失文件可保留
  既有 interrupted-uninstall recovery 语义；symlink、目录、hash 不同均返回 `BTAG-UNINSTALL-MODIFIED`。

### R3：兼容性与验证

- 复用第 009 轮 target/host apply lock；不扩大为外部 host 注册、跨主机锁或 OS sandbox。
- 不增加第三方依赖，兼容 Python 3.8、Windows/POSIX，MIT 许可证和 CLI/API 形状不变。
- 更新派生 manifests，并通过完整三解释器、lint、独立性/doctor 与 clean-wheel 验收。

## 3. 非功能约束

- 修改范围限于 installer、installer 测试、本轮文档和派生 manifests。
- R1 主证据必须在临时 target 内构造 legacy path-traversal manifest，证明旧实现会删除 target 外 marker；
  修复后 marker 必须保持不变。

## 4. 需求追踪

| 需求 | 主验收证据 |
| --- | --- |
| R1 | `../victim` tampered manifest、symlink manifest、duplicate/unknown entry 拒绝且无外部删除 |
| R2 | same-byte adapter symlink 的 install preview 与 uninstall 拒绝，原链接/manifest/target 保留 |
| R3 | installer lifecycle/concurrency 回归、三解释器、clean wheel 与 manifests |
