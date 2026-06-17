# AGENTS.md

Two kinds of "agent" read this file:

1. **Agents *using* LeanProbe** — an LLM agent that calls the LeanProbe MCP server to
   verify Lean. Start at [Using LeanProbe](#using-leanprobe).
2. **Agents/contributors *working on* this repo** — see [Working on this repo](#working-on-this-repo).

---

# Working on this repo

LeanProbe is a Python package (`src/lean_probe/`) exposing a Python API, a CLI, and an
MCP stdio server, backed by [LeanInteract](https://github.com/augustepoiroux/LeanInteract).

**Setup**

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

**Checks (all must pass; CI runs these):**

```bash
ruff check src tests          # lint
ruff format --check src tests # formatting (run `ruff format src tests` to fix)
mypy src
pytest -q                     # unit tests use a fake LeanInteract backend
```

The optional real-LeanInteract test is gated and needs Lean/Lake + a built project:

```bash
LEAN_PROBE_RUN_INTEGRATION=1 pytest tests/test_integration.py -q
```

**Module layout:** `segmentation` (file → header + declaration chunks), `projects`
(Lake/REPL discovery), `errors` (error codes + hints), `payloads` (response shaping,
`feedback_lean`, the shared `ok` logic), `sessions` (LeanInteract lifecycle + the
single-shot `run_command`), `probe` (the `LeanProbe` orchestrator). `core` is a
backwards-compatible facade that re-exports the public names.

**Conventions:**

- Keep LeanProbe independent of downstream projects (no project-specific code).
- The public Python API (`LeanProbe`, `LeanIncrementalSegment`, `segment_file`) is
  importable from the package root — keep it backwards compatible.
- If you change tool semantics, payload fields, or tool names, update the "Using
  LeanProbe" section below and the server `instructions`/`TOOL_NAMES` in
  `mcp_server.py` together (a test asserts the tool table here matches the server).
- Release: bump `version` in `pyproject.toml`, update `CHANGELOG.md`, then push a
  `vX.Y.Z` tag — `release.yml` builds and publishes to PyPI.

---

# Using LeanProbe

LeanProbe gives coding agents fast Lean 4 feedback through a warm
[LeanInteract](https://github.com/augustepoiroux/LeanInteract) REPL. Use it in
the inner loop to verify Lean code far faster than `lake build`. LeanProbe never
edits files; apply accepted code yourself and run `lake build` as the final
whole-project gate before committing.

The MCP server also advertises a condensed version of this guide in its
`instructions` field, so a connected agent gets the essentials on connect.

## Tool Selection

| Tool | Use When | Main Result |
|---|---|---|
| `lean_check` | You have a standalone Lean snippet (imports + code) and want to know if it is valid. The default tool. | Diagnostics and `ok` (valid, no `sorry`). |
| `lean_check_target` | Checking or replacing one named declaration inside a project file; you want the fast warm-environment path. | Pass/fail plus Lean messages; optional tactics/`feedback_lean`. |
| `lean_status` | Setup is uncertain, or you want to pay cold-start up front. | Readiness (project root, REPL, sessions); `warm=true` boots the REPL. |
| `lean_proof_state` | Exploring a goal from code containing `sorry`. | A `session_id` and one proof-state id per `sorry`. |
| `lean_tactic` | Applying one tactic to a proof state. | New goals/proof state, or `ok=true` when Completed. |
| `lean_close_proof` | Finished with a proof-state session. | Releases the session's REPL process. |

`lean_check` is the low-friction default: no file path or declaration name
needed. Reach for `lean_check_target` when you are iterating on one declaration
inside a project file, because it reuses the file's warm prior environment and is
typically tens of milliseconds after the first call.

## Reading Results

Every tool returns a JSON object. Read two fields, in order:

- `success`: did the tool run. `false` means an environment problem (no project
  root, file not found, timeout, REPL crash). Read `error_code` and `hint` and
  fix that first; do not interpret it as a Lean result.
- `ok`: did Lean accept the code. `success=true` with `ok=false` is a real Lean
  rejection — inspect `messages`. `ok=true` means it elaborated with no errors
  and no `sorry`; warnings alone do not flip `ok`.

Scope is the submitted code plus its prepared environment, not the whole
project, so `lake build` remains the final acceptance gate.

Other common fields: `valid_without_sorry`, `has_errors`, `has_sorry`,
`messages` (with chunk-local `start`/`end` and file-adjusted
`file_start`/`file_end`), `tactics`, `feedback_lean`, `project_root` (the root
LeanProbe selected), `elapsed_s`, and `cache`.

### `error_code` and `hint`

On `success=false`, branch on `error_code` (stable) rather than `error` (free
text). Every failure also carries a one-line `hint` telling you what to do next.
Codes: `no_project_root`, `file_not_found`, `target_not_found`,
`replacement_not_a_declaration`, `lean_interact_unavailable`,
`lean_interact_start_failed`, `header_failed`, `prior_decl_failed`,
`dead_server`, `session_dead`, `unknown_session`, `timeout`, `backend_error`.

## Project Root (`cwd`)

`cwd` is optional. LeanProbe auto-detects the nearest Lake project
(`lakefile.lean`/`lakefile.toml`) from the file, then from the server's working
directory. On failure you get `error_code="no_project_root"` and a `hint` naming
what to pass: set `cwd` to the absolute directory holding the lakefile and retry.
An explicit `cwd` must be inside a Lake project. `import Mathlib` resolves only
if that project depends on Mathlib.

## The `replacement` Rule

`replacement` on `lean_check_target` must be a **complete declaration** — the
full signature **and** body, e.g. `theorem foo : P := by ...`. A bare proof body
is rejected with `error_code="replacement_not_a_declaration"`. When in doubt,
pass the whole snippet to `lean_check` instead.

## Latency

The first call after startup pays cold-start (REPL boot plus import
elaboration; tens of seconds for Mathlib). Allow a generous client timeout on
the first call, or call `lean_status` with `warm=true` once to pre-boot.
Subsequent `lean_check_target` calls on the same file reuse the warm environment.
Proof-state and session ids live only inside the running server process;
recreate them after a restart.

## `feedback_lean`

`lean_check_target` with `with_feedback=true` (and any result that includes
tactics) returns `feedback_lean`: the checked Lean with compact inline comments
carrying diagnostics and proof states. It is model-readable context for the next
attempt, not a patch to save back to source.

Each diagnostic is one `-- <glyph> <severity>: <message>` line above the
relevant source line (`✗` error, `⚠` warning, `ℹ` info); each proof state is a
`-- goal: <state>` line. There is no block-comment wrapper, and a goal that is
already shown inside an error on the same line is omitted as redundant.

```lean
-- goal: x y : Nat ⊢ x + y = y + x
theorem add_comm_candidate (x y : Nat) : x + y = y + x := by
  -- ✗ error: unsolved goals x y : Nat ⊢ y + x = y + x
  rw [Nat.add_comm]
  -- ⚠ warning: this tactic is never executed
  rfl
```

Long diagnostics and proof states are truncated; read `messages` and `tactics`
for the raw structured data.

## Limitations

Declarations inside a `mutual ... end` block are not individually targetable —
LeanProbe keeps the whole block as one prior-context chunk because Lean
elaborates it as a unit. Targeting a name found inside a mutual block returns
`error_code="target_not_found"` with a hint explaining this.

LeanProbe checks the submitted chunk against its prepared environment, not the
whole project. Use `lake build`/CI for whole-file or whole-project acceptance.

## Relation to lean-lsp MCP servers

LeanProbe and LSP-backed servers such as `lean-lsp-mcp` are complementary. Use
lean-lsp for editor-like navigation — goal at a cursor position, hover, lemma
search (loogle/leansearch), references, code actions. Use LeanProbe for fast
"does this compile?" verification of snippets and declaration replacements
against a warm environment.

## Environment variables

The CLI flags map to env vars for MCP/process configuration:

- `LEAN_PROBE_LAKE_PATH` — path to the `lake` executable (else `PATH`, then elan).
- `LEAN_PROBE_AUTO_BUILD` — `1` to let LeanInteract build the project. Keep `0`
  for stdio MCP clients (build output on stdout corrupts the JSON-RPC stream).
- `LEAN_PROBE_LOCAL_REPL_PATH` — use a specific local Lean REPL checkout.
- `LEAN_PROBE_VERBOSE` — `1` for verbose LeanInteract setup (stderr).

## Recommended Workflows

### Iterating on one declaration

1. If setup is uncertain, call `lean_status` with `cwd`.
2. Call `lean_check_target` with `file` and `name` for each candidate
   `replacement` (a complete declaration).
3. On `ok=false`, inspect `messages`; if that is not enough, retry with
   `with_feedback=true` and feed `feedback_lean` + `messages` into the next try.
4. After writing accepted code to disk, run `lake build` for whole-project scope.

### Checking an ad-hoc snippet

1. Call `lean_check` with the full snippet (include the imports it needs).
2. Read `ok`; on failure inspect `messages`.

### Tactic exploration

1. `lean_proof_state` with a snippet containing `sorry`.
2. `lean_tactic` with one tactic against a returned proof-state id; repeat until
   `proof_status="Completed"`, or rewrite the declaration and `lean_check_target`.
3. `lean_close_proof` when finished. If `error_code="session_dead"`, call
   `lean_proof_state` again.

## Operating Rules Snippet

```text
Default to lean_check for any standalone Lean snippet. Use lean_check_target with
a COMPLETE replacement declaration (signature + body), not a bare proof body, for
fast repeated checks of a project file. Treat success=false as an environment
problem (read error_code + hint); treat success=true, ok=false as a Lean
rejection and inspect messages. cwd is optional and auto-detected; on
no_project_root pass cwd=<dir with lakefile>. The first call is slow (cold REPL);
call lean_status warm=true to pre-boot. Run lake build as the final gate.
```
