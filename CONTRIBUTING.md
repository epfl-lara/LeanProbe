# Contributing

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

LeanProbe tests use fake LeanInteract backends by default. Real Lean tests are
opt-in.

## Checks

```bash
python -m pytest -q
python -m build
```

Run the optional real LeanInteract smoke test with:

```bash
LEAN_PROBE_RUN_INTEGRATION=1 python -m pytest tests/test_integration.py -q
```

## Development Notes

- Keep LeanProbe independent of downstream projects.
- Preserve the MCP tool names in `src/lean_probe/mcp_server.py`.
- Update `AGENT.md` when tool semantics or payload fields change.
- Keep benchmark raw outputs out of git; `benchmark_results/` is ignored.
