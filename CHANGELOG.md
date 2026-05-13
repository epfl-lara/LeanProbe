# Changelog

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
