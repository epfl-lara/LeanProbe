# LeanProbe

LeanProbe is a standalone Lean 4 feedback package for coding agents. It keeps a
LeanInteract-backed REPL warm, reuses the elaborated imports and prior
declarations in a file, and checks only the declaration an agent is currently
editing.

LeanProbe is not the final verifier. Use it for fast inner-loop feedback, then
run `lake env lean File.lean`, `lake build`, or CI before accepting a change.

## Tools

The public MCP tool names are:

- `lean_probe_prepare`: warm imports/header and prior declarations.
- `lean_probe_check`: check one declaration or replacement declaration.
- `lean_probe_feedback`: return diagnostics, tactic ranges, goal states, and
  `feedback_lean`.
- `lean_probe_state`: create a proof state from Lean code containing `sorry`.
- `lean_probe_step`: apply one tactic to a proof state.

## Why It Is Faster

Agents often make many related Lean checks inside the same file: try a
replacement declaration, inspect diagnostics or proof state, try the next
replacement, then move to another declaration nearby. Repeated full-file checks
pay import/header/prior-declaration cost every time.

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

The benchmark therefore measures two different claims:

- repeated target checks: prepare env before one declaration, then repeatedly
  check replacements for that declaration;
- sequential same-file checks: prepare a header once, then advance declaration by
  declaration with env reuse.

## Install

```bash
python -m pip install -e ".[dev]"
```

Requirements:

- Python 3.10+
- Lean 4 and Lake through `elan`
- `git`
- a Lean project that already builds, or `--auto-build` when LeanInteract should
  build it

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

## Benchmark Files

LeanProbe ships standalone Mathlib benchmark examples under `examples/lean/`.
They are not copied from, imported from, or coupled to any external agent
project. Run them from any existing Mathlib Lake project with `--cwd`.

| File | Targets |
| --- | --- |
| `examples/lean/analysis_real.lean` | `abs_sub_le_abs_add_abs`, `abs_abs_sub_abs_le_abs_sub`, `dist_triangle_real`, `lipschitz_abs_one`, `continuous_shifted_square` |
| `examples/lean/algebra_order.lean` | `sq_add_sq_nonneg`, `two_mul_le_sq_add_sq`, `sq_sub_sq_factor`, `cube_add_expansion`, `square_le_self_on_unit_interval` |
| `examples/lean/sets_functions.lean` | `preimage_inter_eq`, `preimage_subset_preimage`, `image_subset_of_mapsTo`, `injective_from_left_inverse`, `surjective_from_right_inverse` |
| `examples/lean/number_theory_nat.lean` | `nat_add_cancel_bench`, `nat_mul_pos_bench`, `nat_mod_lt_bench`, `nat_square_eq_mul`, `nat_dvd_trans_bench` |

The suite file `examples/benchmark_cases.json` lists all 20 targets with labels,
groups, sizes, and descriptions. Raw benchmark JSON is written to
`benchmark_results/`, which is ignored by git.

## Verification Surfaces

The built-in benchmark compares only standalone, reproducible surfaces:

- terminal `lake env lean`: canonical full-file verification of a temp file
  containing the candidate replacement;
- LeanProbe prepare: wall-clock time to build env before the target;
- LeanProbe warm check: target declaration only, using cached env before target;
- LeanProbe feedback: same target check with tactic/proof-state metadata;
- LeanProbe no-cache check: fresh LeanProbe/LeanInteract server per attempt;
- same-file cutoff Lake: temp file with header plus declarations up to cutoff;
- same-file cutoff LeanInteract cumulative: one header env, full growing
  declaration prefix each cutoff;
- same-file cutoff LeanInteract delta: one header env, next declaration chunk
  only.
- optional external command: any user-provided shell verifier/wrapper timed
  with the same temp full file.

Lean LSP and proof-context tools are useful diagnostics surfaces, but they are
not equivalent final verifiers unless they ultimately run canonical Lean/Lake.
Compare project-specific wrappers through `--external-command` or an
out-of-tree adapter; LeanProbe itself stays independent.

## Current Results

Date: May 13, 2026.

- Lean: `4.30.0-rc2` (`3dc1a088b6d2d8eafe25a7cd7ec7b58d731bd7cc`).

Hardware matters more than the OS label for these timings, especially because
full-file Lake checks and LeanInteract server startup are CPU and memory-cache
sensitive.

| Platform label | Machine | CPU / SoC | Cores / threads | Memory | Extra CPU details |
| --- | --- | --- | ---: | ---: | --- |
| macOS | MacBook Pro `Mac16,7` | Apple M4 Pro | 14 cores, no SMT reported; 10 performance + 4 efficiency | 24 GB unified memory | Darwin 25.4.0, arm64, Python 3.12.12 |
| Linux `larapc2` | single-socket workstation | Intel Core i7-14700KF | 20 cores / 28 threads | 62 GiB RAM, 8 GiB swap | max 5.6 GHz, L2 28 MiB, L3 33 MiB, Linux 6.8.0-111-generic, Python 3.13.9 |

Run policy: 1 measured run per target, 0 benchmark warmups, warm Lake caches from
prior example validation, feedback enabled, no-cache baseline enabled. Prepare
time is shown separately and included in break-even/amortized speedups. The Lake
baseline writes a temp full file and runs `lake env lean`.

### Repeated Target Checks

macOS:

| Group | Cases | Lake avg | Prepare avg | Check avg | Feedback avg | No-cache avg | No-cache / warm |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `analysis_real` | 5 | 3.893s | 6.024s | 0.039s | 0.022s | 4.014s | 139.7x |
| `algebra_order` | 5 | 3.900s | 3.683s | 0.048s | 0.039s | 3.987s | 106.7x |
| `sets_functions` | 5 | 3.708s | 3.502s | 0.008s | 0.007s | 3.766s | 454.7x |
| `number_theory_nat` | 5 | 3.731s | 3.478s | 0.011s | 0.006s | 3.776s | 420.0x |

Linux:

| Group | Cases | Lake avg | Prepare avg | Check avg | Feedback avg | No-cache avg | No-cache / warm |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `analysis_real` | 5 | 2.276s | 2.315s | 0.025s | 0.024s | 2.412s | 103.2x |
| `algebra_order` | 5 | 2.301s | 2.317s | 0.046s | 0.043s | 2.516s | 78.2x |
| `sets_functions` | 5 | 2.233s | 2.257s | 0.011s | 0.009s | 2.383s | 245.8x |
| `number_theory_nat` | 5 | 2.199s | 2.217s | 0.009s | 0.008s | 2.390s | 322.0x |

macOS per-target detail:

| Case | File | Lake | Prepare | Check | Feedback | No-cache | Break-even | 3 tries | 10 tries |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `analysis_abs_sub` | `analysis_real.lean` | 4.072s | 15.791s | 0.085s | 0.030s | 3.797s | 4 | 0.76x | 2.45x |
| `analysis_abs_reverse` | `analysis_real.lean` | 3.679s | 3.510s | 0.017s | 0.014s | 4.628s | 1 | 3.10x | 10.00x |
| `analysis_dist_triangle` | `analysis_real.lean` | 3.788s | 3.652s | 0.036s | 0.030s | 3.723s | 1 | 3.02x | 9.44x |
| `analysis_lipschitz_abs` | `analysis_real.lean` | 3.725s | 3.531s | 0.028s | 0.017s | 3.978s | 1 | 3.09x | 9.77x |
| `analysis_continuous_square` | `analysis_real.lean` | 4.203s | 3.638s | 0.029s | 0.018s | 3.943s | 1 | 3.38x | 10.70x |
| `algebra_sq_nonneg` | `algebra_order.lean` | 3.759s | 3.478s | 0.066s | 0.045s | 4.094s | 1 | 3.07x | 9.08x |
| `algebra_two_mul` | `algebra_order.lean` | 3.720s | 3.717s | 0.056s | 0.049s | 4.138s | 2 | 2.87x | 8.70x |
| `algebra_sq_factor` | `algebra_order.lean` | 3.668s | 3.834s | 0.020s | 0.014s | 3.980s | 2 | 2.83x | 9.09x |
| `algebra_cube_add` | `algebra_order.lean` | 4.027s | 3.759s | 0.027s | 0.025s | 3.843s | 1 | 3.15x | 10.00x |
| `algebra_unit_square` | `algebra_order.lean` | 4.327s | 3.629s | 0.069s | 0.060s | 3.882s | 1 | 3.38x | 10.02x |
| `sets_preimage_inter` | `sets_functions.lean` | 3.774s | 3.499s | 0.010s | 0.009s | 3.810s | 1 | 3.21x | 10.49x |
| `sets_preimage_subset` | `sets_functions.lean` | 3.632s | 3.527s | 0.007s | 0.005s | 3.624s | 1 | 3.07x | 10.10x |
| `sets_mapsTo_image` | `sets_functions.lean` | 3.652s | 3.520s | 0.009s | 0.007s | 3.594s | 1 | 3.09x | 10.12x |
| `sets_left_inverse` | `sets_functions.lean` | 3.642s | 3.457s | 0.008s | 0.004s | 3.976s | 1 | 3.14x | 10.30x |
| `sets_right_inverse` | `sets_functions.lean` | 3.841s | 3.508s | 0.008s | 0.009s | 3.826s | 1 | 3.26x | 10.71x |
| `nat_add_cancel` | `number_theory_nat.lean` | 3.668s | 3.482s | 0.013s | 0.008s | 3.680s | 1 | 3.13x | 10.16x |
| `nat_mul_pos` | `number_theory_nat.lean` | 3.623s | 3.448s | 0.007s | 0.005s | 3.683s | 1 | 3.13x | 10.30x |
| `nat_mod_lt` | `number_theory_nat.lean` | 3.921s | 3.466s | 0.007s | 0.005s | 3.950s | 1 | 3.37x | 11.09x |
| `nat_square_eq_mul` | `number_theory_nat.lean` | 3.790s | 3.492s | 0.021s | 0.009s | 3.718s | 1 | 3.20x | 10.24x |
| `nat_dvd_trans` | `number_theory_nat.lean` | 3.651s | 3.502s | 0.007s | 0.005s | 3.847s | 1 | 3.11x | 10.22x |

The first analysis row includes the coldest LeanInteract server setup observed
in this run. That is why its prepare time is much higher and why it needs four
check attempts to break even. The table keeps it because hiding cold-start
noise would make the benchmark less transparent.

### Sequential Same-File Checks

Run policy: 1 measured run per file, sequential execution, 5 declaration
cutoffs per file. All Lake and LeanInteract cutoff statuses matched and all
rows succeeded.

| Platform | File | Cutoffs | Lake temp cutoffs | LI cumulative | LI delta seq | Delta speedup |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| macOS | `analysis_real.lean` | 5 | 23.694s | 4.086s | 3.812s | 6.22x |
| macOS | `algebra_order.lean` | 5 | 18.830s | 4.311s | 3.854s | 4.89x |
| macOS | `sets_functions.lean` | 5 | 19.129s | 3.809s | 3.691s | 5.18x |
| macOS | `number_theory_nat.lean` | 5 | 18.664s | 4.017s | 3.644s | 5.12x |
| Linux | `analysis_real.lean` | 5 | 11.374s | 2.538s | 2.339s | 4.86x |
| Linux | `algebra_order.lean` | 5 | 11.645s | 2.892s | 2.541s | 4.58x |
| Linux | `sets_functions.lean` | 5 | 11.244s | 2.277s | 2.299s | 4.89x |
| Linux | `number_theory_nat.lean` | 5 | 11.382s | 2.285s | 2.284s | 4.98x |

## Reproduce

Validate the standalone example files:

```bash
lake env lean /path/to/LeanProbe/examples/lean/analysis_real.lean
lake env lean /path/to/LeanProbe/examples/lean/algebra_order.lean
lake env lean /path/to/LeanProbe/examples/lean/sets_functions.lean
lake env lean /path/to/LeanProbe/examples/lean/number_theory_nat.lean
```

Run the target suite:

```bash
PYTHONPATH=src python -m lean_probe.cli benchmark-suite \
  --cases-file examples/benchmark_cases.json \
  --cwd /path/to/mathlib-lake-project \
  --runs 1 --warmups 0 --include-feedback --include-no-cache \
  --results-dir benchmark_results/standalone-local-$(date +%F) \
  --pretty
```

Run one sequential same-file benchmark:

```bash
PYTHONPATH=src python -m lean_probe.cli benchmark-file \
  examples/lean/analysis_real.lean \
  --cwd /path/to/mathlib-lake-project \
  --runs 1 \
  --results-dir benchmark_results/standalone-local-$(date +%F) \
  --pretty
```

Run Python tests:

```bash
PYTHONPATH=src python -m pytest -q
```

Additional validation performed for the May 13, 2026 numbers:

- every positive example file passed `lake env lean`;
- all 20 repeated target benchmark cases returned `success=true`;
- all 4 sequential same-file benchmark files reported matching successful Lake and
  LeanInteract cutoff status;
- the same Python tests and benchmark suite passed on `larapc2`;
- one intentionally broken replacement for `nat_mul_pos_bench` returned
  `ok=false`, `has_errors=true`, a concrete type-mismatch diagnostic, and
  non-empty `feedback_lean`.

## Output Shape

`lean_probe_check` and `lean_probe_feedback` return JSON-compatible dictionaries:

- `ok`: true only when Lean accepts the target without `sorry`;
- `messages`: Lean diagnostics with both chunk-local and file-global positions;
- `tactics`: tactic text, ranges, goals, proof states, and used constants;
- `feedback_lean`: target declaration with inline feedback comments;
- `cache`: header/prior-declaration environment reuse metadata;
- `elapsed_s`: wall-clock time for the check.

## Relationship To LeanInteract

LeanInteract provides the REPL process, incremental elaboration, command
responses, proof states, and tactic stepping. LeanProbe packages one
agent-oriented workflow on top: same-file declaration targeting, warm prior
environments, replacement checking, and MCP-friendly feedback.

See LeanInteract: https://github.com/augustepoiroux/LeanInteract
