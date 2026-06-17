# LeanProbe

[![PyPI](https://img.shields.io/pypi/v/lean-probe.svg)](https://pypi.org/project/lean-probe/)

Fast Lean 4 proof feedback for AI agents — an MCP server, CLI, and Python API.

LeanProbe keeps a Lean REPL warm and reuses the elaborated environment, so
repeated checks in a file come back in tens of milliseconds instead of the
seconds a fresh `lake build` or `lake env lean` costs. It never edits files —
run `lake build` as the final whole-project gate. Built on
[LeanInteract](https://github.com/augustepoiroux/LeanInteract).

## Quickstart

Install (the MCP server is included):

```bash
pip install lean-probe          # or run with no install: uvx lean-probe mcp
```

Add it to **Claude Code**:

```bash
claude mcp add lean-probe --env LEAN_PROBE_AUTO_BUILD=0 -- lean-probe mcp
```

Now ask the agent to check Lean — e.g. *"use lean_check on `theorem t : 2 + 2 =
4 := by norm_num`"*. Or straight from the terminal:

```bash
lean-probe check --cwd /path/to/lake-project --code "example : 2 + 2 = 4 := rfl"
```

## Requirements

- Python 3.10+.
- Lean 4 + Lake via [elan](https://github.com/leanprover/elan), with `lake` on
  `PATH` (or set `LEAN_PROBE_LAKE_PATH`).
- A built Lake project to check against (with Mathlib if your code imports it).

The first call boots the REPL and elaborates imports (tens of seconds for
Mathlib); after that, checks are sub-second — call `lean_status` with
`warm=true` to pay that cost up front. Keep `LEAN_PROBE_AUTO_BUILD=0` for MCP
clients: build output on stdout would corrupt the JSON-RPC stream, so build the
project from a terminal first.

## Add to other clients

**Codex** (`~/.codex/config.toml`):

```toml
[mcp_servers.lean-probe]
command = "lean-probe"
args = ["mcp"]
tool_timeout_sec = 600          # the first Mathlib call is slow

[mcp_servers.lean-probe.env]
LEAN_PROBE_AUTO_BUILD = "0"
```

**Any MCP client** (generic `mcpServers` JSON):

```json
{
  "mcpServers": {
    "lean-probe": { "command": "lean-probe", "args": ["mcp"], "env": { "LEAN_PROBE_AUTO_BUILD": "0" } }
  }
}
```

If the client launches the server outside your environment, use an absolute path
to `lean-probe`, or `"command": "uvx", "args": ["lean-probe", "mcp"]`.

## Tools

On connect the server advertises usage `instructions` and exposes six tools:

| Tool | Purpose |
|---|---|
| `lean_check` | Verify any standalone snippet — the default. |
| `lean_check_target` | Check or replace a declaration in a project file (warm, sub-second). |
| `lean_status` | Readiness; `warm=true` pre-boots the REPL. |
| `lean_proof_state` · `lean_tactic` · `lean_close_proof` | Explore a `sorry` tactic by tactic. |

Read a result with two fields: **`success`** = the tool ran; **`ok`** = Lean
accepted the code (no errors, no `sorry`). On failure, `error_code` + `hint` say
what to do next. See [AGENTS.md](AGENTS.md) for the full contract — parameters,
`feedback_lean`, and every error code.

## Without MCP

CLI:

```bash
lean-probe status --cwd /path/to/lake-project
lean-probe check-target File.lean my_theorem --cwd /path/to/lake-project --pretty
```

Python:

```python
from lean_probe import LeanProbe

probe = LeanProbe()
result = probe.check_target("File.lean", theorem_id="my_theorem", cwd="/path/to/lake-project")
print(result["ok"], result["elapsed_s"])
```

## Benchmarks

Warm cached checks run in tens of milliseconds versus roughly 2–4s for a
full-file Lake check — about 9–14× faster for sequential same-file work. See
[BENCHMARKS.md](BENCHMARKS.md) for methodology and full numbers.

## More

- [AGENTS.md](AGENTS.md) — the full MCP contract (using LeanProbe) and the
  contributor guide (working on it).
- [BENCHMARKS.md](BENCHMARKS.md) — benchmark methodology and results.
- [CONTRIBUTING.md](CONTRIBUTING.md) — dev setup and checks.
