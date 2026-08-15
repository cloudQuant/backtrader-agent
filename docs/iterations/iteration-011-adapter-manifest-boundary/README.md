# 迭代 011：Adapter manifest 与链接完整性

第 009 轮已串行化 apply-time install/uninstall，但最终审计发现卸载仍把 target 内的普通 JSON manifest
作为路径清单直接信任：伪造的 `..` relative path 可让旧实现读取并删除 target 外的匹配文件；已安装 adapter
被替换为相同内容的 symlink 也会被误当作未修改并移除。此轮把 manifest 解释和文件身份收紧为受控边界。

| 文档 | 用途 |
| --- | --- |
| [需求文档](requirements.md) | 定义 manifest、path 与文件身份契约 |
| [设计文档](design.md) | 定义 allowlist manifest 解析与拒绝策略 |
| [验收文档](acceptance.md) | 定义路径逃逸、symlink red test 与发行门 |
| [实施计划](implementation-plan.md) | 测试先行的实现路径 |

## 边界

- 仅覆盖本产品生成的 adapter install manifest 和本产品允许的 adapter relative paths；不执行外部 host
  注册命令，也不改变既有 target/host lifecycle lock。
- manifest 不能成为任意删除指令：未知 schema/host/path、重复文件、非法 hash、symlink/nonregular
  manifest 都保守拒绝。
- 不引入依赖、不改变 CLI 形状或 MIT 许可证；继续兼容 Python 3.8、POSIX/Windows。

## 验收结论

已通过。旧实现会按照伪造的 `../victim.txt` manifest 删除 target 外 marker；现在先完成受控 allowlist
解析，拒绝后没有任何删除。same-byte adapter symlink、manifest symlink 和 malformed manifest 均稳定拒绝；
三解释器、静态检查、独立性审计、doctor 与 clean-wheel 14-cell 矩阵均通过，完整证据见[验收文档](acceptance.md)。
