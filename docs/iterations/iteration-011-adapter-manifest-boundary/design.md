# 迭代 011：Adapter manifest 与链接完整性设计文档

## 1. Manifest 是声明，不是删除脚本

在 host-specific lifecycle lock 内，uninstall 先验证 manifest path 本身是非 symlink 常规文件，再解析并
验证其结构。每个记录的 relative path 都必须落在由当前 host adapter definition 派生出的精确 allowlist，
且 `files` 集合必须无重复并覆盖 allowlist。只有完成这一步，代码才把 relative path 映射到 target 下的
文件。

```text
manifest path safe?
  no  -> BTAG-UNINSTALL-MANIFEST, zero writes
  yes -> schema/host/files allowlist safe?
           no  -> BTAG-UNINSTALL-MANIFEST, zero writes
           yes -> each target regular + non-symlink + hash match?
                    no -> BTAG-UNINSTALL-MODIFIED, zero writes
                    yes -> unlink listed files, then manifest
```

缺失的 allowlisted target 是先前中断卸载的合法恢复状态，仍允许继续删除其余文件和 manifest；但任何存在的
unsafe target 都阻止整个 mutation。

## 2. Install 的对称预检

install 的 preview 和 apply 共用对已存在 path 的身份判断。`exists() or is_symlink()` 捕获 dangling link；
只有普通、非 symlink 且字节相同的文件才可以标为 `unchanged`。这与 create-or-verify 的 apply-time
安全保证一致，避免 preview 给出不可执行的成功计划。

## 3. 测试设计

测试先以旧实现安装 claude adapter，再把 manifest 改为包含 `../victim.txt` 和其 hash。旧 uninstall 会删除
临时 target 外的 marker；修复后必须在 manifest 验证阶段失败，marker、adapter 和 manifest 均保留。
另测 manifest symlink、同内容 adapter symlink 的 preview/uninstall、非法 entries。真实第 009 轮
spawn lifecycle tests 继续覆盖锁的线性化，确保本轮不回退并发保护。
