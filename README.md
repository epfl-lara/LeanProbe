# LeanProbe

[![PyPI](https://img.shields.io/pypi/v/lean-probe.svg?cacheSeconds=3600)](https://pypi.org/project/lean-probe/)

Fast Lean 4 proof feedback for AI agents — an MCP server, CLI, and Python API.

LeanProbe keeps a Lean REPL warm and reuses the elaborated environment, so
repeated checks in a file come back in tens of milliseconds instead of the
seconds a fresh `lake build` or `lake env lean` costs. It never edits files —
run `lake build` as the final whole-project gate. Built on
[LeanInteract](https://github.com/augustepoiroux/LeanInteract).

Scoped declaration parsing and the linear response reader belong to LeanProbe,
so direct Python and MCP calls need no application-level backend patches.
The 0.4.1 development build includes these optimizations and bounded worker pools.
Python clients can require `API_REVISION >= 1` before depending on this contract.

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

That is the whole regular setup — `pip install lean-probe` is fully functional on
its own and writes nothing outside its own package.

**Optional — install the usage skill.** So your agents know the LeanProbe tool
contract without you pasting it, you can additionally install it as a skill. It
ships inside the wheel (no repo clone), and this step is purely opt-in — a plain
`pip install` never touches `~/.claude` or `~/.codex`:

```bash
lean-probe install-skill        # → ~/.claude/skills + ~/.codex/skills (whichever exist)
```

This drops the [LeanProbe skill](src/lean_probe/skill/SKILL.md) into each present
client as `skills/lean-probe/SKILL.md`. Use `--client claude|codex` to force one,
`--skills-dir PATH` for a project-local `.claude/skills`, or `--dry-run` to preview.
The skill only documents the tools — keep the `claude mcp add` / Codex config above
so the `lean-probe` MCP server is actually connected.

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
what to do next. See the [LeanProbe skill](src/lean_probe/skill/SKILL.md) for the
full contract — parameters, `feedback_lean`, and every error code.

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
probe.close()
```

## Warm worker lifecycle and freshness

LeanProbe bounds workers separately for file target checks (4 by default),
standalone snippet checks (2), and interactive proof states (16). Each family
evicts its least recently used worker when its limit is reached. A file evicted
from the pool rebuilds its imports and declaration prefix on its next check;
an evicted proof session returns `unknown_session` and must be recreated.
These limits bound process count, not total memory or the environments retained
inside an individual REPL. Set `max_file_sessions`, `max_scratch_sessions`, and
`max_code_sessions` on the Python constructor; MCP clients can set
`LEAN_PROBE_MAX_FILE_SESSIONS`, `LEAN_PROBE_MAX_SCRATCH_SESSIONS`, and
`LEAN_PROBE_MAX_CODE_SESSIONS` to positive integers. `lean_status` reports the
limits and live pools.
Eviction and project/generation cleanup verify worker exit before releasing
ownership or starting replacements. If a worker remains alive or its exit
cannot be verified, the operation returns `session_cleanup_failed` and retains
ownership and the previous generation. Arrange termination and retry cleanup;
partial cleanup counts include only workers verified stopped.
Such workers are quarantined: old-token checks and tactic steps cannot reuse
them or trigger replacement through dead-server recovery. `close()` remains a
best-effort shutdown for ordinary workers; quarantined workers retain ownership
until their exit is verified, including across `close()` calls.

Edits to the target file are checked against its new header and declaration
prefix. Changes to imported source files or compiled artifacts at the same
paths are **not automatically detected**. The caller must first prepare current
dependencies, then supply a new `generation` token to existing MCP check,
target, status, or proof-state tools, or to the corresponding Python methods.
The token is a caller-owned build/dependency identity: LeanProbe does not compute
or validate it and does not trigger a project build by default. A changed
explicit token discards all three worker families for that project, including
interactive proof states, while preserving workers for other projects.
Keep the token unchanged during proof-body iterations against the same prepared
dependencies. Omitting it retains the active token, so existing unversioned
callers retain their previous freshness responsibility.

Python callers can alternatively run `probe.close_project(project_root)` after
preparing changed dependencies to close only that project's workers. Use
`probe.close()` for full shutdown. Warm results still cover the submitted code
and its prepared environment; independently verify installed source and run
`lake build` as the final project gate.

## Benchmarks

Warm cached checks run in tens of milliseconds versus roughly 2–4s for a
full-file Lake check — about 9–14× faster for sequential same-file work. See
[BENCHMARKS.md](BENCHMARKS.md) for methodology and full numbers.

## More

- [SKILL.md](src/lean_probe/skill/SKILL.md) — the full MCP contract (using
  LeanProbe), installable into agents with `lean-probe install-skill`.
- [AGENTS.md](AGENTS.md) — the contributor guide (working on this repo).
- [BENCHMARKS.md](BENCHMARKS.md) — benchmark methodology and results.
- [CONTRIBUTING.md](CONTRIBUTING.md) — dev setup and checks.
