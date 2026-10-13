# Changelog

- Keep LeanInteract warnings on stderr in the native MCP server, preserving clean
  JSON-RPC framing without modifying backend methods or the direct Python API.

## 0.4.1.dev0 - Development

- Bound file target and standalone snippet workers with configurable LRU limits,
  defaulting to 4 and 2. Preserve the existing 16-worker proof-state default;
  expose all three limits through MCP environment variables and status.
- Add Python `close_project(cwd)` for project-scoped cleanup and optional
  caller-owned `generation` identities to Python and existing MCP check,
  target, status, and proof-state APIs. Changing a token invalidates every warm
  worker for that project while preserving other projects. Dependency freshness
  remains caller-owned; no automatic dependency scans or project builds occur.
- Verify worker retirement on pool eviction and project/generation cleanup.
  Retain ownership and the previous generation, and block replacement work,
  if termination fails or exit cannot be verified; report `session_cleanup_failed`.
- Handle scoped declarations, including inline and stacked command wrappers,
  directly in the native parser with bounded declaration-window searches.
- Own the linear REPL response reader in LeanProbe so direct Python and MCP
  use does not require application-level patches of LeanInteract.

## 0.4.0 - 2026-06-26

- Split the agent-facing contract out of `AGENTS.md` into a real, installable
  **skill** at `src/lean_probe/skill/SKILL.md` (YAML frontmatter + the MCP tool
  contract). `AGENTS.md` now holds the contributor guide and points to it.
- New `lean-probe install-skill` command drops the skill into agent clients'
  skills directories — `~/.claude/skills` and `~/.codex/skills` use the same
  `skills/<name>/SKILL.md` layout. By default it installs to every client whose
  home dir exists ("both if both"); `--client claude|codex` forces one (creating
  dirs), `--skills-dir PATH` targets an explicit root, and `--dry-run`/`--print`
  preview. The skill ships inside the wheel, so `pip install lean-probe &&
  lean-probe install-skill` works with no repo checkout.
- The README links to the skill as the single source of truth, and the MCP server
  advertises a condensed version of it in its `instructions` field; a test keeps the
  skill's tool table in sync with `TOOL_NAMES`.

## 0.3.1 - 2026-06-17

- The MCP server is now part of the base install: `mcp` moved from the `[mcp]`
  extra into the core dependencies. `pip install lean-probe`, `uvx lean-probe mcp`,
  and `claude mcp add … -- lean-probe mcp` work with no extra. The `[mcp]` extra is
  kept as a no-op so existing commands/configs keep working.
- Renamed `AGENT.md` to `AGENTS.md` (the recognized convention) and split it into
  "Using LeanProbe" (the agent/MCP contract) and "Working on this repo" (contributor
  setup, checks, module layout, release).
- README: frictionless install + copy-paste MCP setup for Claude Code, Codex, and
  generic clients.

## 0.3.0 - 2026-06-17

Agent-integration redesign of the MCP surface (breaking). The MCP server now
advertises usage `instructions` on connect and reports its real version in
`serverInfo` (previously the `mcp` library version), and the tool set is
renamed/streamlined for agent usability.

- New MCP tools (`lean_*`): `lean_check` (verify any standalone snippet — the new
  low-friction default), `lean_check_target` (merges the old `check`/`feedback`
  via a `with_feedback` flag; `theorem_id` is now `name`), `lean_status`
  (was `lean_probe_capabilities`, with `warm` to pre-boot the REPL),
  `lean_proof_state`, `lean_tactic`, `lean_close_proof`. The standalone
  `lean_probe_prepare` MCP tool is dropped (warming is implicit; `prepare_file`
  remains on the Python API). Tools carry read-only/destructive annotations.
- `replacement` is now validated: a bare proof body is rejected with
  `error_code="replacement_not_a_declaration"` instead of a cryptic parse error.
- Every failure payload carries an actionable `hint`; `no_project_root` lists the
  directories searched. Payloads echo the resolved `project_root`.
- Fixed a dead-server recovery bug: a restarted REPL is now rebuilt from a fresh
  environment instead of replaying stale `env` ids.
- Fixed segmentation of `where`/nested declarations so a helper using a
  declaration keyword is no longer torn off into a bogus top-level chunk.
- MCP defaults are stdio-safe (auto-build/verbose off) and `lake` is auto-detected
  (PATH, then elan) so an empty-env client still works.
- CLI mirrors the tools (`status`, `check`, `check-target`, `proof-state`,
  `prepare`); benchmark commands are imported lazily.
- Internals split into focused modules (`segmentation`, `projects`, `errors`,
  `payloads`, `sessions`, `probe`); `core` remains a compatibility facade and
  `LeanProbe`/`LeanIncrementalSegment`/`segment_file` stay importable from the
  package root. The Python API is unchanged and backwards compatible.

## 0.2.2 - 2026-05-13

- Added `lean_probe_capabilities` and `lean-probe capabilities` to expose
  readiness, project-root detection, selected REPL path, and live session state.
- Documented how LeanProbe complements LSP-backed Lean MCP tools such as
  `lean-lsp-mcp`.

## 0.2.1 - 2026-05-13

- Documented that stdio MCP clients should keep `LEAN_PROBE_AUTO_BUILD=0` and
  build Lean projects before starting LeanProbe, because build output on stdout
  can corrupt MCP transport framing.

## 0.2.0 - 2026-05-13

- Expanded declaration segmentation for modifiers, attributes, additional Lean
  declaration kinds, Unicode names, and universe-parameter declarations.
- Treat `mutual ... end` as one prior-context chunk instead of incorrectly
  targeting the inner declarations as standalone chunks.
- Added `lean_probe_close_state`, bounded proof-state session eviction,
  shutdown cleanup, and `session_dead` reporting for stale tactic sessions.
- Moved MCP support to the `mcp` extra, added structured error codes, stricter
  `--cwd` handling, `py.typed`, CI, release publishing, lint/type checks, and
  wheel smoke testing.
- Improved benchmark scenarios so partial `sorry` checks are generated only for
  declaration chunks with `:= by` proof bodies.

## 0.1.0

- Initial standalone LeanProbe package, CLI, and MCP server.
- LeanInteract-backed file segmentation, cached target checks, proof-state
  creation, tactic stepping, and feedback annotation.
- Benchmark suite and standalone Mathlib examples.
