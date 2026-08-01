# Security Policy

`backtrader-agent` is an offline-first Backtrader strategy-authoring runtime.
It is **not** a trading system: it never connects to a live broker, never
places a real order, never downloads market data, and never promises investment
returns. This document describes the security properties the project enforces
and its known limits.

## Reporting a vulnerability

Report security issues privately by opening a private security advisory on the
repository, or contact the maintainers directly. Do not open a public issue for
security-sensitive defects. Please include:

- a minimal description of the issue and its impact,
- the `BTAG-*` diagnostic code(s) if any are produced,
- the product version (`backtrader-agent doctor --json`) and Backtrader engine
  version, and
- steps to reproduce with offline fixtures only.

## Enforced properties

- **No candidate import in the host process.** Strategy candidates are validated
  by Python AST only. They are never imported into the agent process.
- **No dynamic execution.** `exec`, `eval`, `compile`, `__import__`,
  reflection (`getattr`/dunder), filesystem access (`open`), and process/network
  libraries are rejected by the validator.
- **Capability allowlists.** Imports, Backtrader APIs, local strategy symbols,
  and environment keys use exact allowlists. Live broker/store APIs are
  forbidden.
- **Confined paths.** External paths are an opaque root ID plus a relative
  path. Resolution rejects absolute paths, `..`, symlink escape, devices, and
  non-regular files. Dataset bytes are read with before/after stat checks and
  stored in immutable SHA-256 content-addressed storage.
- **Distinct approvals.** Apply (change) and execute (run) capabilities are
  separate hash-bound tokens. A change token cannot authorize a run, and vice
  versa. Each follows a persisted request, explicit local grant, signed token,
  and atomic consume.
- **Fixed child-process profile.** Runs execute only `run.py` or a generated
  test through a fixed argv with `shell=False`, a minimal environment that does
  not forward `HOME`, a timeout, and resource/output limits. Source and data are
  re-hashed before execution.
- **Authenticated provenance.** Only candidates with a renderer-owned, locally
  signed provenance record and matching session/spec/dataset/artifact approvals
  may run. Forged manifests, cross-session token reuse, and tampered provenance
  records fail closed.
- **Recoverable sessions.** Every state transition is recorded in an append-only
  hash chain; recovery accepts only a verified prefix and never guesses past
  damage. Terminal sessions never silently reactivate.

## Known limits (defense in depth, not a sandbox)

- The controlled child process is **not** an OS sandbox. POSIX resource limits
  are applied where available, but this is not equivalent to a container or
  OS-level isolation.
- **Network isolation is not OS-verified.** The runtime does not download data
  or talk to brokers by design, but it does not prove network isolation at the
  OS level.
- Pandas/custom-line workflows accept only already-materialized tabular text.
  Pickle and arbitrary Python object deserialization are rejected by design.
- The validator is static. A candidate that passes validation is not guaranteed
  safe in every conceivable runtime; the controlled child process and approval
  model are the remaining layers.

## Secret handling

- Never log API keys, broker credentials, or secrets.
- API keys/secrets are out of scope for this offline runtime. If you integrate
  it into a larger system, keep secrets in environment variables or a secret
  manager and validate their presence at startup of that outer system.
- Diagnostics use stable `BTAG-*` codes and redact absolute paths and full
  tracebacks before display.
