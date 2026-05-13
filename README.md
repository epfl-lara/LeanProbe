# LeanProbe

LeanProbe is a standalone Python package, CLI, and MCP server for fast Lean 4
feedback in coding-agent workflows. It uses
[LeanInteract](https://github.com/augustepoiroux/LeanInteract) as its execution
backend, keeps a Lean REPL warm, reuses elaborated imports and prior
declarations, and checks a named target declaration or replacement chunk.

LeanProbe returns Lean diagnostics, warnings, `sorry` detection, tactic
metadata, goal states, and inline `feedback_lean`. The result is a real Lean
response for the checked code chunk. Whole-file or whole-project gates can still
run `lake env lean File.lean`, `lake build`, or CI when that broader scope is
required.

## MCP Tools

LeanProbe exposes the MCP server name `lean-probe` and the tools
`lean_probe_prepare`, `lean_probe_check`, `lean_probe_feedback`,
`lean_probe_state`, `lean_probe_step`, and `lean_probe_close_state`.

For agent-facing tool contracts, parameter meanings, result-field semantics,
and `feedback_lean` examples, see [AGENT.md](AGENT.md).

## Why It Is Faster

Automated Lean workflows often perform many related checks inside one file:
try a replacement declaration, inspect diagnostics or proof state, try the next
replacement, then move to a nearby declaration. A repeated full-file terminal
check pays import, header, and prior-declaration elaboration cost each time.

LeanProbe separates that cost:

```text
prepare header/imports/prior declarations -> env before target
env before target + checked declaration -> diagnostics/proof states
env before target + next checked declaration -> diagnostics/proof states
```

For sequential same-file checks, the useful pattern is:

```text
header/import env -> next declaration chunk -> next env -> next declaration chunk
```

The benchmark suite measures two separate cases:

- repeated target checks: prepare env before one declaration, then repeatedly
  check replacements for that declaration;
- sequential same-file checks: prepare a header once, then advance declaration by
  declaration with env reuse.

## Install

LeanProbe is a Python package that talks to Lean through LeanInteract. It does
not install Lean, Lake, or Mathlib for you. The Python environment running
LeanProbe must be able to import `lean_interact`, and `lake` must be available
on `PATH` or passed with `--lake-path`.

Required:

- Python 3.10 or newer.
- Lean 4 and Lake installed through `elan`.
- `git`, used by Lean/Lake dependency workflows.
- A Lean/Lake project to run checks in. For the bundled examples, that project
  must have Mathlib available because the examples start with `import Mathlib`.
- A built Lean project, or `--auto-build` when you want LeanInteract to build it
  before checking.

Recommended local development setup:

```bash
python -m pip install lean-probe
```

Install MCP support when you want to run the MCP server:

```bash
python -m pip install "lean-probe[mcp]"
```

Editable checkout for development:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

Check the Python package and CLI:

```bash
python -c "import lean_probe, lean_interact; print('ok')"
lean-probe --version  # lean-probe X.Y.Z
```

Check that Lean/Lake are visible:

```bash
lake --version
lean --version
```

Run LeanProbe by pointing `--cwd` at a Lake project that can import the
dependencies used by the file being checked:

```bash
lean-probe check examples/lean/number_theory_nat.lean nat_mul_pos_bench \
  --cwd /path/to/mathlib-lake-project \
  --pretty
```

If the target project does not already have LeanInteract's REPL support built,
either let LeanInteract build it with `--auto-build` or pass an existing REPL
checkout with `--local-repl-path`.
If `--cwd` is supplied, it must be inside a Lake project; LeanProbe fails
loudly instead of falling back to another project.

For MCP use, configure the agent to run `lean-probe mcp` from this same Python
environment. If the agent launches MCP servers outside your activated shell, use
the absolute path to `.venv/bin/lean-probe` in the MCP configuration.
Set `LEAN_PROBE_LAKE_PATH`, `LEAN_PROBE_LOCAL_REPL_PATH`,
`LEAN_PROBE_AUTO_BUILD`, or `LEAN_PROBE_VERBOSE` to configure the MCP server
without CLI flags.

After an editable development install, run the package tests from the
repository with `python -m pytest -q`.

## CLI

```bash
lean-probe prepare /path/to/File.lean --cwd /path/to/lake-project --theorem-id my_theorem

lean-probe check /path/to/File.lean my_theorem \
  --cwd /path/to/lake-project \
  --replacement-file /tmp/candidate.lean \
  --pretty

lean-probe feedback /path/to/File.lean my_theorem \
  --cwd /path/to/lake-project \
  --pretty

lean-probe benchmark /path/to/File.lean my_theorem \
  --cwd /path/to/lake-project \
  --runs 5 --warmups 1 --include-feedback --include-no-cache \
  --external-command 'lake-direct=lake env lean {file}' \
  --results-dir benchmark_results/local-$(date +%F) \
  --pretty

lean-probe benchmark-suite \
  --cases-file examples/benchmark_cases.json \
  --cwd /path/to/mathlib-lake-project \
  --runs 5 --warmups 1 --include-feedback --include-no-cache \
  --results-dir benchmark_results/local-$(date +%F) \
  --pretty

lean-probe benchmark-file /path/to/File.lean \
  --cwd /path/to/lake-project \
  --runs 3 \
  --results-dir benchmark_results/local-$(date +%F) \
  --pretty
```

`--include-no-cache` is deliberately useful: it times a fresh LeanProbe /
LeanInteract server per attempt and shows the cost of using LeanInteract without
persistent env reuse.

`--external-command NAME=COMMAND` is the independent escape hatch for comparing
other verifiers or wrappers. The command runs from `--cwd`; placeholders are
`{file}` for the temp full file, `{original}` for the source file, `{cwd}` for
the Lake project root, and `{theorem}` for the target declaration.

## Python

```python
from lean_probe import LeanProbe

probe = LeanProbe()
probe.prepare_file("/path/to/File.lean", cwd="/path/to/lake-project", theorem_id="my_theorem")

result = probe.check_target(
    "/path/to/File.lean",
    cwd="/path/to/lake-project",
    theorem_id="my_theorem",
    replacement="""
theorem my_theorem : True := by
  trivial
""",
)
print(result["ok"], result["elapsed_s"])
```

For tactic-by-tactic exploration:

```python
state = probe.proof_state_from_code("theorem ex (n : Nat) : n = n := by sorry")
proof_state = state["sorries"][0]["proof_state"]
step = probe.tactic_step(state["session_id"], proof_state, "rfl")
print(step["proof_status"])
```

## MCP

Run the MCP server over stdio:

```bash
lean-probe mcp
```

Example MCP configuration:

```json
{
  "mcpServers": {
    "lean-probe": {
      "command": "lean-probe",
      "args": ["mcp"]
    }
  }
}
```

Agents should call `lean_probe_prepare` at the start of a same-file checking
turn, then use `lean_probe_check` after concrete edits. When ordinary
diagnostics do not explain the failure, call `lean_probe_feedback` and inspect
`messages`, `tactics`, and `feedback_lean`.

For precise agent-facing tool contracts, result-field semantics, and
`feedback_lean` examples, see [AGENT.md](AGENT.md).

## Benchmark Files

LeanProbe ships standalone Mathlib benchmark examples under `examples/lean/`.
The compact files are hand-written smoke and micro-benchmark cases. The
`tcs_*` files are longer extracts from the
[CodaBench TCS Proving competition](https://www.codabench.org/competitions/16161/).
The concrete Lean source was taken from the public companion repository
[epfl-lara/icml-26-lean-challenges](https://github.com/epfl-lara/icml-26-lean-challenges),
with source headers retained. These files exercise more realistic algorithm and
graph-development code without adding a runtime dependency on either source.
Run all examples from any existing Mathlib Lake project by passing that project
as `--cwd`.

| File | Targets |
| --- | --- |
| `examples/lean/analysis_real.lean` | `abs_sub_le_abs_add_abs`, `abs_abs_sub_abs_le_abs_sub`, `dist_triangle_real`, `lipschitz_abs_one`, `continuous_shifted_square` |
| `examples/lean/algebra_order.lean` | `sq_add_sq_nonneg`, `two_mul_le_sq_add_sq`, `sq_sub_sq_factor`, `cube_add_expansion`, `square_le_self_on_unit_interval` |
| `examples/lean/sets_functions.lean` | `preimage_inter_eq`, `preimage_subset_preimage`, `image_subset_of_mapsTo`, `injective_from_left_inverse`, `surjective_from_right_inverse` |
| `examples/lean/number_theory_nat.lean` | `nat_add_cancel_bench`, `nat_mul_pos_bench`, `nat_mod_lt_bench`, `nat_square_eq_mul`, `nat_dvd_trans_bench` |
| `examples/lean/tcs_binary_heap.lean` | selected binary heap definitions such as `heapify`, `extract_min`, `insert`, `merge`, and `remove` |
| `examples/lean/tcs_treap_analysis.lean` | `uniform_prob_sum_one`, `perm_prob_sum_one` |
| `examples/lean/tcs_weighted_graph_prefix.lean` | selected weighted graph helpers and definitions through `Sym2order` |

The suite file `examples/benchmark_cases.json` lists all 40 targets with labels,
groups, sizes, and descriptions. Raw benchmark JSON is written to
`benchmark_results/`, which is ignored by git.

## Verification Surfaces

The built-in benchmarks compare standalone, reproducible verification surfaces:

- terminal `lake env lean`: canonical full-file verification of a temp file
  containing the candidate replacement;
- Probe prepare: wall-clock time to build env before the target;
- Probe cached check: target declaration only, using cached env before target;
- Probe cached feedback: same target check with tactic/proof-state metadata;
- Probe fresh check: fresh LeanProbe/LeanInteract server per attempt;
- same-file Lake growing-prefix checks: for each partial/full scenario, temp
  file with header plus accepted prior declarations plus the current
  declaration;
- same-file Lake full-file checks: for each partial/full scenario, temp file
  containing the whole source file with only the current declaration replaced;
- same-file Probe cached checks: one LeanInteract server reuses header and
  prior declaration environments across partial/full scenarios;
- same-file Probe fresh checks: fresh LeanProbe/LeanInteract server per
  scenario;
- optional external command: any user-provided shell verifier/wrapper timed
  with the same temp full file.

Lean LSP, MCP, and proof-context tools are useful diagnostics surfaces. Compare
project-specific wrappers through `--external-command` or an out-of-tree adapter
that exits nonzero on hard failure; LeanProbe itself stays independent.

## Benchmark Experiments

The README reports two benchmark shapes. They answer different questions and
should not be mixed.

### Repeated Target Checks

Question measured: "If an agent tries several complete replacements for the
same declaration, how much does a prepared environment help?"

Per target, the benchmark does this:

1. Build a temporary full file containing the candidate replacement and time
   `lake env lean`.
2. Start LeanProbe, prepare the environment before the target declaration, and
   report that time as `Probe prepare avg` or `Probe prepare env`.
3. Check the target replacement against that cached environment and report that
   time as `Probe cached check avg` or `Probe cached check`.
4. Optionally request tactic/proof-state metadata and report that as
   `Probe cached feedback avg` or `Probe cached feedback`.
5. Repeat the LeanProbe check with a fresh server and no cache reuse to show
   what LeanInteract costs without persistent state.

The important total for repeated attempts is:

```text
Probe total for n attempts = prepare time + n * cached check time
Lake total for n attempts = n * full-file Lake time
```

`Attempts to beat Lake` is the smallest integer `n` where the Probe total is
lower than the Lake total. `Amortized speedup, 3 attempts` and
`Amortized speedup, 10 attempts` use the same formula at fixed attempt counts.

### Sequential Same-File Checks

Question measured: "If an agent works through several declarations in the same
file, can the checker reuse the file-local environment instead of starting over
for each scenario?"

For each targetable declaration in the file, the benchmark checks the complete
declaration. When the declaration has a `:= by` proof body, it also checks a
partial scenario:

1. a partial version containing `sorry`, which should be accepted by Lean with
   `sorry` detected;
2. the complete version, which must be accepted without `sorry`.

LeanProbe advances the cached environment only after the complete version
succeeds. The Lake baselines rerun terminal checks for each scenario:

- `Lake growing-prefix total`: `lake env lean` on a temp file containing the
  header, already accepted prior declarations, and the current scenario.
- `Lake full-file total`: `lake env lean` on a temp full file where only the
  current declaration is replaced by the current scenario.
- `Probe cached total`: one LeanProbe/LeanInteract server reusing header and
  prior-declaration environments across all scenarios.
- `Probe fresh total`: a fresh LeanProbe/LeanInteract server for every
  scenario, showing the cost when there is no cache reuse.

## Current Results

Last refreshed: May 13, 2026.

- Lean: `4.30.0-rc2` (`3dc1a088b6d2d8eafe25a7cd7ec7b58d731bd7cc`).
- In the tables below, `Probe` means LeanProbe.

| Environment | Machine | CPU / SoC | Cores / threads | Memory | Runtime and CPU details |
| --- | --- | --- | ---: | ---: | --- |
| macOS | MacBook Pro `Mac16,7` | Apple M4 Pro | 14 cores, no SMT reported; 10 performance + 4 efficiency | 24 GB unified memory | Darwin 25.4.0, arm64, Python 3.12.12 |
| Linux `larapc2` | single-socket workstation | Intel Core i7-14700KF | 20 cores / 28 threads | 62 GiB RAM, 8 GiB swap | max 5.6 GHz, L2 28 MiB, L3 33 MiB, Linux 6.8.0-111-generic, Python 3.13.9 |

Run policy for repeated-target tables: 1 measured run per target, 0 benchmark
warmups, warm Lake caches from prior example validation, feedback enabled, and
fresh-server baseline enabled. Prepare time is shown separately and included in
break-even and amortized speedups. The Lake baseline writes a temp full file and
runs `lake env lean`.

### Repeated Target Checks

macOS:

| Example group | Targets | Lake full-file avg | Probe prepare avg | Probe cached check avg | Probe cached feedback avg | Probe fresh check avg | Fresh check / cached check |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `analysis_real` | 5 | 3.893s | 6.024s | 0.039s | 0.022s | 4.014s | 139.7x |
| `algebra_order` | 5 | 3.900s | 3.683s | 0.048s | 0.039s | 3.987s | 106.7x |
| `sets_functions` | 5 | 3.708s | 3.502s | 0.008s | 0.007s | 3.766s | 454.7x |
| `number_theory_nat` | 5 | 3.731s | 3.478s | 0.011s | 0.006s | 3.776s | 420.0x |

Linux:

| Example group | Targets | Lake full-file avg | Probe prepare avg | Probe cached check avg | Probe cached feedback avg | Probe fresh check avg | Fresh check / cached check |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `analysis_real` | 5 | 2.276s | 2.315s | 0.025s | 0.024s | 2.412s | 103.2x |
| `algebra_order` | 5 | 2.301s | 2.317s | 0.046s | 0.043s | 2.516s | 78.2x |
| `sets_functions` | 5 | 2.233s | 2.257s | 0.011s | 0.009s | 2.383s | 245.8x |
| `number_theory_nat` | 5 | 2.199s | 2.217s | 0.009s | 0.008s | 2.390s | 322.0x |

Column guide for repeated-target summary tables:

- `Lake full-file avg`: average wall time to write a temp full file with the
  target declaration replaced, then run `lake env lean` on that file.
- `Probe prepare avg`: average wall time for `lean_probe_prepare`; this
  warms imports/header and declarations before the target.
- `Probe cached check avg`: average `lean_probe_check` time after prepare,
  checking only the target declaration against the cached environment.
- `Probe cached feedback avg`: average `lean_probe_feedback` time after prepare,
  including diagnostics plus tactic/proof-state metadata.
- `Probe fresh check avg`: average time for the same target check with a new
  LeanProbe/LeanInteract server and no prior cache reuse.
- `Fresh check / cached check`: `Probe fresh check avg / Probe cached check avg`;
  larger values mean cache reuse matters more.

Per-target repeated-check rows are in [BENCHMARKS.md](BENCHMARKS.md).

### TCS Challenge Repeated Target Checks

Run policy: same as the compact repeated-target tables above. These rows cover
the 20 longer examples derived from the CodaBench TCS Proving source material.
Raw JSON was written under `benchmark_results/tcs-local-2026-05-13/` and
`benchmark_results/tcs-linux-2026-05-13/`.

Grouped summary:

| Platform | Example group | Targets | Lake full-file avg | Probe prepare avg | Probe cached check avg | Probe cached feedback avg | Probe fresh check avg | Fresh check / cached check |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| macOS | `tcs_binary_heap` | 9 | 2.576s | 2.931s | 0.049s | 0.042s | 2.589s | 155.9x |
| macOS | `tcs_treap_analysis` | 2 | 2.082s | 2.219s | 0.034s | 0.034s | 2.181s | 77.8x |
| macOS | `tcs_weighted_graph` | 9 | 2.617s | 2.461s | 0.031s | 0.028s | 2.603s | 194.9x |
| Linux | `tcs_binary_heap` | 9 | 1.886s | 1.807s | 0.054s | 0.051s | 1.877s | 103.2x |
| Linux | `tcs_treap_analysis` | 2 | 1.495s | 1.441s | 0.036s | 0.040s | 1.560s | 53.1x |
| Linux | `tcs_weighted_graph` | 9 | 1.771s | 1.683s | 0.032s | 0.034s | 1.761s | 127.5x |

Per-target TCS rows are in [BENCHMARKS.md](BENCHMARKS.md).

### Sequential Same-File Checks

Run policy: 1 measured run per file, sequential execution, 5 declarations per
file. This benchmark models a file-level checking session:

1. check imports/header;
2. for each targetable declaration with a `:= by` proof body, check a partial
   `sorry` version and confirm `sorry` is detected without hard errors;
3. check the full declaration and require valid-without-sorry;
4. advance the cached environment only after the full declaration succeeds.

Raw JSON for these rows was written under
`benchmark_results/file-level-local-full-2026-05-13/` and
`benchmark_results/file-level-linux-full-2026-05-13/`. Those directories are
ignored by git, so reruns do not pollute the package history.

| Platform | File | Declarations | Scenarios | Lake growing-prefix total | Lake full-file total | Probe cached total | Probe fresh total | Speedup vs growing-prefix Lake | Speedup vs full-file Lake | Speedup vs fresh Probe |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| macOS | `analysis_real.lean` | 5 | 10 | 67.992s | 40.216s | 4.775s | 44.699s | 14.24x | 8.42x | 9.36x |
| macOS | `algebra_order.lean` | 5 | 10 | 46.870s | 48.163s | 4.339s | 40.953s | 10.80x | 11.10x | 9.44x |
| macOS | `sets_functions.lean` | 5 | 10 | 45.421s | 42.237s | 3.916s | 37.797s | 11.60x | 10.79x | 9.65x |
| macOS | `number_theory_nat.lean` | 5 | 10 | 36.474s | 36.648s | 3.789s | 36.736s | 9.63x | 9.67x | 9.70x |
| Linux | `analysis_real.lean` | 5 | 10 | 22.765s | 22.958s | 2.515s | 24.890s | 9.05x | 9.13x | 9.90x |
| Linux | `algebra_order.lean` | 5 | 10 | 23.186s | 23.489s | 2.547s | 25.147s | 9.10x | 9.22x | 9.87x |
| Linux | `sets_functions.lean` | 5 | 10 | 22.679s | 22.567s | 2.384s | 24.195s | 9.51x | 9.47x | 10.15x |
| Linux | `number_theory_nat.lean` | 5 | 10 | 22.593s | 22.829s | 2.301s | 24.086s | 9.82x | 9.92x | 10.47x |

Column guide for sequential same-file tables:

- `Declarations`: number of declarations walked in that file.
- `Scenarios`: number of checks performed. Here each declaration contributes two
  scenarios: a partial declaration containing `sorry`, then the complete
  declaration.
- `Lake growing-prefix total`: total terminal time for `lake env lean` on temp
  prefix files containing header + already accepted prior declarations +
  current scenario.
- `Lake full-file total`: total terminal time for `lake env lean` on temp full
  files where only the current declaration is replaced by the scenario text.
- `Probe cached total`: total time for one LeanProbe/LeanInteract server
  walking the same scenarios while reusing the same-file environment.
- `Probe fresh total`: total time for LeanProbe checks with a fresh
  LeanProbe/LeanInteract server per scenario.
- `Speedup vs growing-prefix Lake`: `Lake growing-prefix total /
  Probe cached total`.
- `Speedup vs full-file Lake`: `Lake full-file total / Probe cached total`.
- `Speedup vs fresh Probe`: `Probe fresh total / Probe cached total`;
  this isolates the value of cache reuse within LeanProbe itself.

## Reproduce

Validate the standalone example files:

```bash
lake env lean /path/to/LeanProbe/examples/lean/analysis_real.lean
lake env lean /path/to/LeanProbe/examples/lean/algebra_order.lean
lake env lean /path/to/LeanProbe/examples/lean/sets_functions.lean
lake env lean /path/to/LeanProbe/examples/lean/number_theory_nat.lean
lake env lean /path/to/LeanProbe/examples/lean/tcs_binary_heap.lean
lake env lean /path/to/LeanProbe/examples/lean/tcs_treap_analysis.lean
lake env lean /path/to/LeanProbe/examples/lean/tcs_weighted_graph_prefix.lean
```

Run the target suite:

```bash
lean-probe benchmark-suite \
  --cases-file examples/benchmark_cases.json \
  --cwd /path/to/mathlib-lake-project \
  --runs 1 --warmups 0 --include-feedback --include-no-cache \
  --results-dir benchmark_results/standalone-local-$(date +%F) \
  --pretty
```

Run one sequential same-file benchmark. By default this includes terminal Lake
prefix checks, terminal Lake full-file checks, cached LeanProbe checks, and
no-cache LeanProbe checks:

```bash
lean-probe benchmark-file \
  examples/lean/analysis_real.lean \
  --cwd /path/to/mathlib-lake-project \
  --runs 1 \
  --results-dir benchmark_results/standalone-local-$(date +%F) \
  --pretty
```

To compare another verifier, pass it as a shell command. `{file}` is the temp
full candidate file for the current partial/full scenario:

```bash
lean-probe benchmark-file \
  examples/lean/analysis_real.lean \
  --cwd /path/to/mathlib-lake-project \
  --runs 1 \
  --external-command 'custom-verify=/path/to/verify-file.sh {file}' \
  --pretty
```

MCP tools are usually not shell commands, so benchmark them through a small
adapter script that calls the MCP tool for `{file}`, exits nonzero on hard
failure, and prints a final JSON line with `success`, `ok`, `has_errors`, and
`has_sorry`.

Run Python tests:

```bash
python -m pytest -q
```

Run the optional real LeanInteract smoke test:

```bash
LEAN_PROBE_RUN_INTEGRATION=1 python -m pytest tests/test_integration.py -q
```

Additional validation performed for the May 13, 2026 numbers:

- every positive example file used for the May 13 benchmark tables passed
  `lake env lean`;
- all 20 compact repeated target benchmark cases returned `success=true`;
- the longer `tcs_*` example files pass `lake env lean`, and all 20 CodaBench
  TCS benchmark cases returned `success=true` with feedback and fresh-server
  baselines on both macOS and `larapc2`;
- all 4 sequential same-file benchmark files reported successful partial-sorry
  and full-without-sorry scenarios for Lake and LeanProbe;
- the same Python tests and benchmark suite passed on `larapc2`;
- one intentionally broken replacement for `nat_mul_pos_bench` returned
  `ok=false`, `has_errors=true`, a concrete type-mismatch diagnostic, and
  non-empty `feedback_lean`.

## Output Shape

`lean_probe_check` and `lean_probe_feedback` return JSON-compatible dictionaries:

- `success`: false for tool/project/backend failures;
- `ok`: true only when Lean accepts the target without `sorry`;
- `error_code`: stable machine-readable failure code when `success=false`;
- `timed_out`: true when the backend failure was classified as a timeout;
- `messages`: Lean diagnostics with both chunk-local and file-global positions;
- `tactics`: tactic text, ranges, goals, proof states, and used constants;
- `feedback_lean`: target declaration with inline feedback comments;
- `cache`: header/prior-declaration environment reuse metadata;
- `elapsed_s`: wall-clock time for the check.

Current `error_code` values include `no_project_root`, `file_not_found`,
`target_not_found`, `lean_interact_unavailable`, `lean_interact_start_failed`,
`header_failed`, `prior_decl_failed`, `dead_server`, `session_dead`,
`unknown_session`, `timeout`, and `backend_error`.

See [AGENT.md](AGENT.md) for the complete MCP output contract, including
`success` versus `ok`, proof-state stepping, and how agents should consume
`feedback_lean`.

Declarations inside `mutual ... end` blocks are included as prior context for
later targets, but the individual declarations inside the mutual block are not
separate LeanProbe targets.

## Backend Dependency

[LeanInteract](https://github.com/augustepoiroux/LeanInteract) is LeanProbe's
primary backend dependency. LeanInteract provides the Lean REPL process,
incremental elaboration, command responses, proof states, tactic stepping, and
the low-level interaction API.

LeanProbe builds on that backend with package-level ergonomics for coding
agents: file segmentation, same-file declaration targeting, warm prior
environments, replacement checks, feedback annotation, CLI commands, MCP tools,
and reproducible benchmark harnesses.
