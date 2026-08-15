# 迭代 008：Immutable Record 并发重放设计文档

## 1. canonical exact helper

新增 canonical bytes/json helper，逻辑为：

```text
expected canonical bytes
  -> try atomic_write_bytes(create_only=True)
       success: return created=True
       BTAG-WRITE-EXISTS: securely re-read target
  -> target is regular, non-symlink and byte-identical?
       yes: return created=False
       no: raise caller-specific conflict
```

因此 no-replace 仍是唯一发布动作；“成功重放”只发生在读取赢家完整内容并精确比较之后。helper 不接受
一个通用的成功吞错模式：每个调用者传入其既有 conflict code/message，保留 domain diagnostics。

## 2. 调用者迁移

- Dataset：CAS object 以 normalized bytes 比较，manifest 以 canonical JSON bytes 比较；仅新建 CAS
  object 执行只读 chmod。
- Artifact：每个生成文件、artifact manifest、signed provenance record 均使用 helper；同 revision
  多进程渲染形成相同 artifact，而不依赖 session lock。
- Bound record：用 canonical signed record JSON 比较，保持 signer/record-hash 的完整性。
- Installer：preview 沿用当前预检；apply 再对每个文件及 manifest 执行 helper，并用 created bool 修正
  `changes[].action`，所以竞争 loser 如实返回 `unchanged`。

## 3. 测试设计

四组 worker 都在 canonical `os.link(temporary, destination)` 前用共享 spawn barrier 汇合；旧调用者
中一方收到 `BTAG-WRITE-EXISTS`，新 helper 在同一竞态后读取赢家并返回成功。每组额外断言最终 bytes、
hash/签名或 installer 状态，且 helper 覆盖 mismatch/symlink 冲突而非 silent overwrite。
