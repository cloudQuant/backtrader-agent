# Contributing

Thanks for contributing to `backtrader-agent`. This project is offline-first,
deterministic, and independence-strict: changes must preserve the security
model, the content-addressed manifests, and the reproducible acceptance matrix.

## Development setup

```bash
python -m pip install backtrader pandas jsonschema pytest
python -m pytest tests -q -p no:cacheprovider
python scripts/audit_independence.py
python scripts/doctor.py
python scripts/run_acceptance.py
```

The generated runner imports `pandas` at module load (the Pandas adapters and
the canonical feed assembly path need it). `pip install backtrader` does not
always pull pandas, so install it explicitly.

The tests and acceptance matrix need a Backtrader engine root: a directory
containing `backtrader/__init__.py` and `backtrader/version.py`. It is resolved
automatically; set `BACKTRADER_AGENT_ACCEPTANCE_ENGINE_ROOT` explicitly if
auto-resolution fails (see [README.md](README.md#verification)).

## Before opening a pull request

1. **Keep manifests exact.** After any source, resource, or repository file
   change, regenerate both distribution manifests and commit the result:

   ```bash
   python scripts/build_manifest.py
   ```

   The independence audit (`scripts/audit_independence.py`) and the
   `test_source_distribution_manifest_covers_every_file` test fail closed when a
   manifest drifts. CI also checks that the regenerated manifests match the
   committed files.

2. **Keep tests green.** `python -m pytest tests` must pass, including the
   14-cell acceptance matrix (7 archetypes x `single_test`/`python_bundle`,
   each run in both `runonce` and `runnext`).

3. **Preserve independence.** Do not add imports of `backtrader_mcp`,
   `backtrader_skills`, `fastmcp`, or `mcp`. Do not read `.agents/skills`,
   `backtrader-mcp`, or `backtrader-skills` paths. The independence audit
   enforces this statically.

4. **Do not weaken the security model.** No candidate import in the host
   process, no dynamic execution, no `shell=True`, no live broker/store APIs,
   and distinct apply/run approvals. See [SECURITY.md](SECURITY.md).

## Code style

- Many small, focused files; high cohesion, low coupling.
- Immutable patterns: create new objects, do not mutate.
- Handle errors explicitly with stable `BTAG-*` codes; never swallow errors.
- Validate at boundaries; never trust external input.
- Type hints throughout; functions small.

## Commit messages

Follow conventional commits:

```
<type>: <description>

<optional body>
```

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`.

## Adding a new archetype or adapter

New archetypes/adapter formats are product-scope changes: update
`src/backtrader_agent/contracts.py`, the renderer in `scaffold.py`, the
validator allowlists in `validator.py`, the acceptance matrix expectations in
`scripts/run_acceptance.py`, and the relevant schemas under
`src/backtrader_agent/resources/contracts/`. Then regenerate manifests and run
the full acceptance matrix.
