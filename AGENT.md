# LeanProbe Agent Guide

This guide is written for coding agents that call the LeanProbe MCP server.
It describes when to use each tool, what each result means, and how to use
`feedback_lean` as model-readable Lean feedback.

## Mental Model

LeanProbe checks Lean code through a warm LeanInteract server. For file-targeted
tools, it splits a Lean file into:

- a header/import prefix;
- declaration chunks before the target;
- the target declaration chunk.

`lean_probe_prepare` elaborates the header and, when a target is supplied, the
declarations before that target. The returned environment is cached inside the
MCP server process. `lean_probe_check` and `lean_probe_feedback` then check only
the target declaration or a full replacement declaration against that prepared
environment.

For the code chunk it checks, LeanProbe is a real Lean check: `ok=true` means
Lean accepted that chunk without hard errors and without `sorry`. The scope is
the supplied chunk plus the prepared environment. Use `lake env lean File.lean`,
`lake build`, or CI when you need a whole-file or whole-project gate.

## Tool Selection

| Tool | Use When | Main Result |
|---|---|---|
| `lean_probe_prepare` | Starting repeated checks in one Lean file, or moving to a later target in the same file. | A cached Lean environment before the target. |
| `lean_probe_check` | Testing the current declaration or a candidate full replacement declaration. | Fast pass/fail plus Lean messages and optional tactic metadata. |
| `lean_probe_feedback` | A check failed, or the agent needs proof states, tactic ranges, and annotated Lean context. | Same status fields as check, with tactics and `feedback_lean`. |
| `lean_probe_state` | Exploring a proof state from standalone Lean code containing `sorry`. | A session id and one proof-state id per `sorry`. |
| `lean_probe_step` | Trying one tactic against a proof state returned by `lean_probe_state` or a previous step. | New goals/proof state, or `ok=true` if the proof is completed. |

## Shared Result Fields

Most LeanProbe tools return JSON-compatible dictionaries with these fields:

- `success`: whether the tool/server call completed. `false` means an
  infrastructure problem such as missing project root, missing file, timeout, or
  LeanInteract startup failure.
- `ok`: whether the Lean-level operation succeeded for this tool. For
  `check`/`feedback`, this means the checked declaration was accepted without
  hard errors and without `sorry`.
- `backend`: always `lean_interact`.
- `tool`: always `lean_probe`.
- `action`: `prepare`, `check`, `feedback`, `state`, or `step`.
- `elapsed_s`: wall-clock seconds measured by LeanProbe for this call.
- `error`: infrastructure or backend error text. Inspect this first when
  `success=false`.
- `error_code`: stable machine-readable failure code, such as
  `no_project_root`, `file_not_found`, `target_not_found`,
  `lean_interact_unavailable`, `header_failed`, `prior_decl_failed`,
  `dead_server`, or `timeout`.
- `timed_out`: true when LeanProbe classified the backend failure as a timeout.
- `messages`: Lean diagnostics. Each message includes `severity`, `message`,
  chunk-local `start`/`end`, and file-adjusted `file_start`/`file_end` when a
  file target is checked.
- `output`: compact text summary of diagnostics, suitable for logs.
- `cache`: environment ids and cache metadata. These ids are internal to the
  live MCP server process and should not be persisted.

For `check` and `feedback`, these additional fields matter:

- `valid_without_sorry`: LeanInteract's validity result with `sorry` rejected.
- `has_errors`: whether Lean reported hard errors.
- `has_sorry`: whether the checked chunk used `sorry`.
- `target`: matched declaration name.
- `target_kind`: declaration kind, such as `theorem`, `lemma`, `def`,
  `instance`, or `example`.
- `target_range`: source-file line range for the target declaration.
- `tactics`: tactic text, ranges, goals, proof-state ids, and used constants.
- `feedback_lean`: checked Lean declaration with inline feedback comments.

Interpretation rules:

- If `success=false`, fix the tool/project problem before interpreting Lean
  diagnostics. Use `error_code` for routing and `error` for human-readable
  detail.
- If `success=true` and `ok=false`, LeanProbe ran successfully and Lean rejected
  the checked code or found `sorry`.
- Warnings do not make `ok=false` unless they are accompanied by hard errors or
  `sorry`.
- `start`/`end` positions are local to the checked chunk. `file_start`/`file_end`
  are adjusted back to the original file when LeanProbe knows the target range.

## `feedback_lean`

`feedback_lean` is intended to be placed directly into an agent's next prompt.
It is not a patch that should normally be saved back to the Lean source.

LeanProbe inserts Lean block comments before relevant lines:

```lean
/- <feedback>
-- proof state: x y : Nat ⊢ x + y = y + x
</feedback> -/
theorem add_comm_candidate (x y : Nat) : x + y = y + x := by
  /- <feedback>
  -- type: error, msg: unsolved goals
  </feedback> -/
  rfl
```

Feedback blocks can contain:

- diagnostic messages with severity and message text;
- tactic proof states and goals near the tactic that produced them;
- indentation that matches the surrounding Lean line.

The annotation is intentionally compact. Long diagnostics and large proof
states are truncated so they remain useful in agent context. If the agent needs
the raw structured data, read `messages` and `tactics` directly.

## `lean_probe_prepare`

Purpose: warm the Lean environment for a file, optionally through all
declarations before a target.

Inputs:

- `file_path` (required): absolute path or path relative to `cwd`/project root.
- `theorem_id` (optional): target declaration name. Qualified and unqualified
  names are accepted when they match the segmented file.
- `cwd` (optional): Lean/Lake project root or a path inside the project.
- `timeout_s` (optional, default `60`): LeanInteract timeout.

Typical call:

```json
{
  "file_path": "examples/lean/number_theory_nat.lean",
  "theorem_id": "nat_mul_pos_bench",
  "cwd": "/path/to/mathlib-project"
}
```

Typical successful result:

```json
{
  "success": true,
  "ok": true,
  "action": "prepare",
  "target": "nat_mul_pos_bench",
  "elapsed_s": 1.234,
  "cache": {
    "header_env": 4,
    "env_before": 9,
    "cache_hit": false
  }
}
```

Use this before repeated checks of the same target. `lean_probe_check` can
prepare on demand, but an explicit prepare call makes startup and cache costs
visible.

## `lean_probe_check`

Purpose: check one target declaration quickly against the cached environment
before that target.

Inputs:

- `file_path` (required): Lean source file.
- `theorem_id` (required): target declaration name.
- `replacement` (optional): full replacement declaration chunk. If omitted,
  LeanProbe checks the target declaration currently in the file.
- `include_tactics` (optional, default `false`): request tactic metadata.
- `cwd` and `timeout_s`: same meaning as `prepare`.

The `replacement` must be a complete Lean declaration or equivalent chunk, not
only a proof body. It should include the theorem/lemma/def signature and proof.

Typical call:

```json
{
  "file_path": "examples/lean/number_theory_nat.lean",
  "theorem_id": "nat_mul_pos_bench",
  "cwd": "/path/to/mathlib-project",
  "replacement": "theorem nat_mul_pos_bench (a b : Nat) (ha : 0 < a) (hb : 0 < b) : 0 < a * b := by\n  exact Nat.mul_pos ha hb"
}
```

Typical success:

```json
{
  "success": true,
  "ok": true,
  "action": "check",
  "target": "nat_mul_pos_bench",
  "valid_without_sorry": true,
  "has_errors": false,
  "has_sorry": false,
  "elapsed_s": 0.018,
  "messages": [],
  "feedback_lean": ""
}
```

Typical Lean failure:

```json
{
  "success": true,
  "ok": false,
  "action": "check",
  "target": "nat_mul_pos_bench",
  "valid_without_sorry": false,
  "has_errors": true,
  "has_sorry": false,
  "output": "error: ...",
  "messages": [
    {
      "severity": "error",
      "message": "...",
      "start": {"line": 2, "column": 2},
      "file_start": {"line": 8, "column": 2}
    }
  ]
}
```

On failure, `lean_probe_check` may rerun internally with tactic collection so
the response can include useful `tactics` and `feedback_lean`.

## `lean_probe_feedback`

Purpose: run a target check with tactic collection and return model-readable
feedback.

Inputs:

- `file_path`, `theorem_id`, `replacement`, `cwd`, `timeout_s`: same meaning as
  `lean_probe_check`.

Use this when:

- `lean_probe_check` returns `ok=false` and the diagnostic summary is not enough;
- the next candidate should be guided by local proof states;
- the agent needs annotated Lean text for prompt context.

Typical result fields:

```json
{
  "success": true,
  "ok": false,
  "action": "feedback",
  "target": "nat_mul_pos_bench",
  "messages": [{ "severity": "error", "message": "..." }],
  "tactics": [
    {
      "tactic": "exact ...",
      "goals": "...",
      "proof_state": 12,
      "file_start": {"line": 9, "column": 2},
      "used_constants": []
    }
  ],
  "feedback_lean": "/- <feedback>\\n-- type: error, msg: ...\\n</feedback> -/\\n..."
}
```

`feedback` is usually more expensive than `check` because it asks LeanInteract
for tactic metadata. Prefer `check` for ordinary candidate loops and call
`feedback` when the agent needs richer context.

## `lean_probe_state`

Purpose: create proof states from standalone Lean code containing `sorry`.

Inputs:

- `code` (required): Lean code. Include imports and local context if needed.
- `cwd` (optional): Lean project root for imports and project dependencies.
- `include_tactics` (optional, default `false`): include tactic metadata from
  the command response.
- `timeout_s` (optional, default `60`): LeanInteract timeout.

Typical call:

```json
{
  "cwd": "/path/to/mathlib-project",
  "code": "import Mathlib\n\ntheorem ex (n : Nat) : n = n := by\n  sorry"
}
```

Typical result:

```json
{
  "success": true,
  "ok": true,
  "action": "state",
  "session_id": "6b276d6f-1c0b-42a3-8f7b-0f59aab26742",
  "has_errors": false,
  "sorries": [
    {
      "goal": "n : Nat\n⊢ n = n",
      "proof_state": 3,
      "start": {"line": 4, "column": 2}
    }
  ]
}
```

For this tool, `ok=true` means LeanProbe successfully extracted at least one
proof state from a `sorry` and found no hard errors. It does not mean the proof
is complete.

The `session_id` is held in memory by the running MCP server. If the server
restarts, create a new state session.

MCP server configuration can be supplied through environment variables:
`LEAN_PROBE_LAKE_PATH`, `LEAN_PROBE_LOCAL_REPL_PATH`,
`LEAN_PROBE_AUTO_BUILD`, and `LEAN_PROBE_VERBOSE`.

## `lean_probe_step`

Purpose: apply one tactic to a proof state.

Inputs:

- `session_id` (required): value returned by `lean_probe_state`.
- `proof_state` (required): proof-state id from `state.sorries[*].proof_state`
  or a previous `lean_probe_step` result.
- `tactic` (required): one Lean tactic string, such as `rfl`, `omega`, or
  `exact h`.
- `timeout_s` (optional, default `60`): LeanInteract timeout.

Typical call:

```json
{
  "session_id": "6b276d6f-1c0b-42a3-8f7b-0f59aab26742",
  "proof_state": 3,
  "tactic": "rfl"
}
```

Typical completed result:

```json
{
  "success": true,
  "ok": true,
  "action": "step",
  "proof_status": "Completed",
  "goals": [],
  "elapsed_s": 0.012
}
```

Typical incomplete result:

```json
{
  "success": true,
  "ok": false,
  "action": "step",
  "proof_status": "Incomplete",
  "proof_state": 7,
  "goals": ["..."]
}
```

When `ok=false` and `success=true`, use the returned `proof_state` and `goals`
to decide the next tactic.

## Recommended Workflows

### Repeated Candidate Checks For One Declaration

1. Call `lean_probe_prepare` with `file_path`, `cwd`, and `theorem_id`.
2. For each candidate, call `lean_probe_check` with a complete replacement
   declaration.
3. If `ok=false`, inspect `output` and `messages`.
4. If the next edit is not obvious, call `lean_probe_feedback` and pass
   `feedback_lean` plus structured `messages`/`tactics` into the next model
   prompt.
5. After accepting and writing a candidate to disk, run a whole-file or
   whole-project command when that larger scope matters.

### Sequential Same-File Work

1. Work in source order when possible.
2. Prepare/check the first target.
3. If a replacement is accepted and future declarations should see it, write the
   accepted declaration to the file before checking later targets.
4. Call `lean_probe_prepare` or `lean_probe_check` for the next target. The
   server will reuse the header and valid prior-declaration checkpoints whose
   text has not changed.

If imports, options, namespaces, or earlier declarations change, LeanProbe will
rebuild the affected environment. If the LeanInteract server dies, LeanProbe
tries to restart it and report the error if restart fails.

### Tactic Exploration

1. Call `lean_probe_state` with a small Lean snippet containing `sorry`.
2. Pick a proof-state id from `sorries`.
3. Call `lean_probe_step` with one tactic.
4. Continue with the returned proof-state id until `proof_status` is
   `Completed`, or use the goals to rewrite the full declaration and check it
   with `lean_probe_check`.

## Agent Prompt Snippet

When using LeanProbe, give the agent these operational rules:

```text
Use lean_probe_prepare before repeated checks in a Lean file. Use
lean_probe_check with a complete replacement declaration, not only a proof body.
Treat success=false as a tool/project problem. Treat success=true, ok=false as a
Lean result and inspect messages/output. Call lean_probe_feedback when proof
states or annotated feedback_lean would help the next edit. For final file or
project scope, run lake env lean, lake build, or CI after writing accepted code.
```
