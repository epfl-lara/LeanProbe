# LeanProbe

LeanProbe is a standalone Python package, CLI, and MCP server for fast Lean 4
feedback in coding-agent workflows. It uses
[LeanInteract](https://github.com/augustepoiroux/LeanInteract) as its execution
backend, keeps a Lean REPL warm, reuses elaborated imports and prior
declarations, and checks the declaration currently under edit.

LeanProbe returns Lean diagnostics, warnings, `sorry` detection, tactic
metadata, goal states, and inline `feedback_lean`. The result is a real Lean
response for the checked code chunk. Whole-file or whole-project gates can still
run `lake env lean File.lean`, `lake build`, or CI when that broader scope is
required.

## MCP Tools

The public MCP tool names are:

- `lean_probe_prepare`: warm imports/header and prior declarations.
- `lean_probe_check`: check one declaration or replacement declaration.
- `lean_probe_feedback`: return diagnostics, tactic ranges, goal states, and
  `feedback_lean`.
- `lean_probe_state`: create a proof state from Lean code containing `sorry`.
- `lean_probe_step`: apply one tactic to a proof state.

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

```bash
python -m pip install -e ".[dev]"
```

Requirements:

- Python 3.10+
- Lean 4 and Lake through `elan`
- `git`
- a Lean project that already builds, or `--auto-build` when LeanInteract should
  build the project before checking

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
- LeanProbe prepare: wall-clock time to build env before the target;
- LeanProbe warm check: target declaration only, using cached env before target;
- LeanProbe feedback: same target check with tactic/proof-state metadata;
- LeanProbe no-cache check: fresh LeanProbe/LeanInteract server per attempt;
- same-file Lake prefix checks: for each partial/full scenario, temp file with
  header plus accepted prior declarations plus the current declaration;
- same-file Lake full-file checks: for each partial/full scenario, temp file
  containing the whole source file with only the current declaration replaced;
- same-file LeanProbe cached checks: one LeanInteract server reuses header and
  prior declaration environments across partial/full scenarios;
- same-file LeanProbe no-cache checks: fresh LeanProbe/LeanInteract server per
  scenario;
- optional external command: any user-provided shell verifier/wrapper timed
  with the same temp full file.

Lean LSP, MCP, and proof-context tools are useful diagnostics surfaces. Compare
project-specific wrappers through `--external-command` or an out-of-tree adapter
that exits nonzero on hard failure; LeanProbe itself stays independent.

## Current Results

Date: May 13, 2026.

- Lean: `4.30.0-rc2` (`3dc1a088b6d2d8eafe25a7cd7ec7b58d731bd7cc`).

The platform label identifies the machine used for the run, not a general claim
about that operating system. The hardware details are included because these
benchmarks measure wall-clock latency: full-file Lake checks, LeanInteract
startup, and cached declaration checks can all be affected by CPU generation,
core layout, memory capacity, cache behavior, and existing build artifacts.

| Environment | Machine | CPU / SoC | Cores / threads | Memory | Runtime and CPU details |
| --- | --- | --- | ---: | ---: | --- |
| macOS | MacBook Pro `Mac16,7` | Apple M4 Pro | 14 cores, no SMT reported; 10 performance + 4 efficiency | 24 GB unified memory | Darwin 25.4.0, arm64, Python 3.12.12 |
| Linux `larapc2` | single-socket workstation | Intel Core i7-14700KF | 20 cores / 28 threads | 62 GiB RAM, 8 GiB swap | max 5.6 GHz, L2 28 MiB, L3 33 MiB, Linux 6.8.0-111-generic, Python 3.13.9 |

Run policy: 1 measured run per target, 0 benchmark warmups, warm Lake caches from
prior example validation, feedback enabled, no-cache baseline enabled. Prepare
time is shown separately and included in break-even and amortized speedups. The
Lake baseline writes a temp full file and runs `lake env lean`.

### Repeated Target Checks

macOS:

| Example group | Targets | Full-file Lake avg | LeanProbe prepare avg | Cached check avg | Cached feedback avg | Fresh LeanProbe avg | Fresh / cached |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `analysis_real` | 5 | 3.893s | 6.024s | 0.039s | 0.022s | 4.014s | 139.7x |
| `algebra_order` | 5 | 3.900s | 3.683s | 0.048s | 0.039s | 3.987s | 106.7x |
| `sets_functions` | 5 | 3.708s | 3.502s | 0.008s | 0.007s | 3.766s | 454.7x |
| `number_theory_nat` | 5 | 3.731s | 3.478s | 0.011s | 0.006s | 3.776s | 420.0x |

Linux:

| Example group | Targets | Full-file Lake avg | LeanProbe prepare avg | Cached check avg | Cached feedback avg | Fresh LeanProbe avg | Fresh / cached |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `analysis_real` | 5 | 2.276s | 2.315s | 0.025s | 0.024s | 2.412s | 103.2x |
| `algebra_order` | 5 | 2.301s | 2.317s | 0.046s | 0.043s | 2.516s | 78.2x |
| `sets_functions` | 5 | 2.233s | 2.257s | 0.011s | 0.009s | 2.383s | 245.8x |
| `number_theory_nat` | 5 | 2.199s | 2.217s | 0.009s | 0.008s | 2.390s | 322.0x |

Column guide:

- `Full-file Lake avg`: average wall time to write a temp full file with the
  target declaration replaced, then run `lake env lean` on that file.
- `LeanProbe prepare avg`: average wall time for `lean_probe_prepare`; this
  warms imports/header and declarations before the target.
- `Cached check avg`: average `lean_probe_check` time after prepare, checking
  only the target declaration against the cached environment.
- `Cached feedback avg`: average `lean_probe_feedback` time after prepare,
  including diagnostics plus tactic/proof-state metadata.
- `Fresh LeanProbe avg`: average time for the same target check with a new
  LeanProbe/LeanInteract server and no prior cache reuse.
- `Fresh / cached`: `Fresh LeanProbe avg / Cached check avg`; larger values
  mean cache reuse matters more.

macOS per-target detail:

| Case label | File | Full-file Lake | Prepare env | Cached check | Cached feedback | Fresh check | Break-even checks | Speedup at 3 checks | Speedup at 10 checks |
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

Column guide:

- `Full-file Lake`: one `lake env lean` run on a temp full file for this target.
- `Prepare env`: one `lean_probe_prepare` run for the environment before this
  target.
- `Cached check`: one warm `lean_probe_check` against the prepared environment.
- `Cached feedback`: one warm `lean_probe_feedback` against the prepared
  environment.
- `Fresh check`: one `lean_probe_check` with a fresh LeanProbe/LeanInteract
  server and no cache reuse.
- `Break-even checks`: minimum number of repeated target checks needed for
  `Prepare env + n * Cached check` to be faster than `n * Full-file Lake`.
- `Speedup at 3/10 checks`: `(n * Full-file Lake) / (Prepare env + n * Cached check)`.
  Values below `1.0x` mean the prepare cost has not yet paid off.

The first analysis row includes the coldest LeanInteract server setup observed
in this run. Its prepare time is therefore much higher, and it needs four check
attempts to break even. The row is kept to make cold-start effects visible.

### TCS Challenge Repeated Target Checks

Run policy: same as the compact repeated-target tables above. These rows cover
the 20 longer examples derived from the CodaBench TCS Proving source material.
Raw JSON was written under `benchmark_results/tcs-local-2026-05-13/` and
`benchmark_results/tcs-linux-2026-05-13/`.

Grouped summary:

| Platform | Example group | Targets | Full-file Lake avg | LeanProbe prepare avg | Cached check avg | Cached feedback avg | Fresh LeanProbe avg | Fresh / cached |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| macOS | `tcs_binary_heap` | 9 | 2.576s | 2.931s | 0.049s | 0.042s | 2.589s | 155.9x |
| macOS | `tcs_treap_analysis` | 2 | 2.082s | 2.219s | 0.034s | 0.034s | 2.181s | 77.8x |
| macOS | `tcs_weighted_graph` | 9 | 2.617s | 2.461s | 0.031s | 0.028s | 2.603s | 194.9x |
| Linux | `tcs_binary_heap` | 9 | 1.886s | 1.807s | 0.054s | 0.051s | 1.877s | 103.2x |
| Linux | `tcs_treap_analysis` | 2 | 1.495s | 1.441s | 0.036s | 0.040s | 1.560s | 53.1x |
| Linux | `tcs_weighted_graph` | 9 | 1.771s | 1.683s | 0.032s | 0.034s | 1.761s | 127.5x |

macOS per-target detail:

| Case label | File | Full-file Lake | Prepare env | Cached check | Cached feedback | Fresh check | Break-even checks | Speedup at 3 checks | Speedup at 10 checks |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `tcs_heap_is_min_heap` | `tcs_binary_heap.lean` | 2.669s | 6.597s | 0.026s | 0.012s | 2.326s | 3 | 1.20x | 3.89x |
| `tcs_heap_heapify` | `tcs_binary_heap.lean` | 2.499s | 2.260s | 0.179s | 0.151s | 2.569s | 1 | 2.68x | 6.17x |
| `tcs_heap_get_last` | `tcs_binary_heap.lean` | 2.484s | 2.405s | 0.015s | 0.012s | 2.452s | 1 | 3.04x | 9.72x |
| `tcs_heap_extract_min` | `tcs_binary_heap.lean` | 2.762s | 2.395s | 0.012s | 0.008s | 2.556s | 1 | 3.41x | 10.98x |
| `tcs_heap_heap_min` | `tcs_binary_heap.lean` | 2.485s | 2.415s | 0.016s | 0.015s | 2.520s | 1 | 3.03x | 9.65x |
| `tcs_heap_insert` | `tcs_binary_heap.lean` | 2.520s | 2.448s | 0.009s | 0.008s | 2.540s | 1 | 3.05x | 9.93x |
| `tcs_heap_merge` | `tcs_binary_heap.lean` | 2.535s | 2.491s | 0.160s | 0.151s | 2.659s | 2 | 2.56x | 6.20x |
| `tcs_heap_remove` | `tcs_binary_heap.lean` | 2.589s | 2.631s | 0.009s | 0.007s | 2.863s | 2 | 2.92x | 9.51x |
| `tcs_heap_size` | `tcs_binary_heap.lean` | 2.643s | 2.740s | 0.019s | 0.014s | 2.819s | 2 | 2.83x | 9.02x |
| `tcs_treap_uniform_prob` | `tcs_treap_analysis.lean` | 2.104s | 2.313s | 0.020s | 0.026s | 2.183s | 2 | 2.66x | 8.37x |
| `tcs_treap_perm_prob` | `tcs_treap_analysis.lean` | 2.059s | 2.125s | 0.047s | 0.042s | 2.179s | 2 | 2.73x | 7.93x |
| `tcs_wgraph_subset_list` | `tcs_weighted_graph_prefix.lean` | 2.736s | 2.644s | 0.011s | 0.009s | 2.303s | 1 | 3.07x | 9.93x |
| `tcs_wgraph_memconsrw` | `tcs_weighted_graph_prefix.lean` | 2.454s | 2.227s | 0.032s | 0.017s | 2.371s | 1 | 3.17x | 9.63x |
| `tcs_wgraph_subset_comb` | `tcs_weighted_graph_prefix.lean` | 2.598s | 2.303s | 0.040s | 0.033s | 3.143s | 1 | 3.22x | 9.61x |
| `tcs_wgraph_empty` | `tcs_weighted_graph_prefix.lean` | 2.634s | 2.454s | 0.009s | 0.008s | 2.490s | 1 | 3.19x | 10.35x |
| `tcs_wgraph_subgraph` | `tcs_weighted_graph_prefix.lean` | 2.830s | 2.433s | 0.007s | 0.005s | 2.489s | 1 | 3.46x | 11.31x |
| `tcs_wgraph_from_edge_subset_subgraph` | `tcs_weighted_graph_prefix.lean` | 2.577s | 2.437s | 0.011s | 0.010s | 2.504s | 1 | 3.13x | 10.12x |
| `tcs_wgraph_from_edge_subset` | `tcs_weighted_graph_prefix.lean` | 2.568s | 2.459s | 0.146s | 0.148s | 2.772s | 2 | 2.66x | 6.55x |
| `tcs_wgraph_mst` | `tcs_weighted_graph_prefix.lean` | 2.555s | 2.540s | 0.008s | 0.006s | 2.677s | 1 | 2.99x | 9.75x |
| `tcs_wgraph_sym2order` | `tcs_weighted_graph_prefix.lean` | 2.599s | 2.651s | 0.015s | 0.016s | 2.680s | 2 | 2.89x | 9.28x |

Linux per-target detail:

| Case label | File | Full-file Lake | Prepare env | Cached check | Cached feedback | Fresh check | Break-even checks | Speedup at 3 checks | Speedup at 10 checks |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `tcs_heap_is_min_heap` | `tcs_binary_heap.lean` | 1.883s | 1.650s | 0.016s | 0.013s | 1.645s | 1 | 3.33x | 10.40x |
| `tcs_heap_heapify` | `tcs_binary_heap.lean` | 1.887s | 1.530s | 0.197s | 0.176s | 1.774s | 1 | 2.67x | 5.39x |
| `tcs_heap_get_last` | `tcs_binary_heap.lean` | 1.899s | 1.803s | 0.017s | 0.019s | 1.780s | 1 | 3.07x | 9.62x |
| `tcs_heap_extract_min` | `tcs_binary_heap.lean` | 1.946s | 1.789s | 0.015s | 0.013s | 1.786s | 1 | 3.18x | 10.04x |
| `tcs_heap_heap_min` | `tcs_binary_heap.lean` | 1.891s | 1.772s | 0.024s | 0.019s | 1.819s | 1 | 3.08x | 9.40x |
| `tcs_heap_insert` | `tcs_binary_heap.lean` | 1.821s | 1.824s | 0.010s | 0.009s | 1.845s | 2 | 2.95x | 9.46x |
| `tcs_heap_merge` | `tcs_binary_heap.lean` | 1.907s | 1.862s | 0.177s | 0.180s | 2.034s | 2 | 2.39x | 5.25x |
| `tcs_heap_remove` | `tcs_binary_heap.lean` | 1.929s | 1.984s | 0.009s | 0.008s | 2.101s | 2 | 2.88x | 9.30x |
| `tcs_heap_size` | `tcs_binary_heap.lean` | 1.810s | 2.051s | 0.024s | 0.019s | 2.108s | 2 | 2.56x | 7.90x |
| `tcs_treap_uniform_prob` | `tcs_treap_analysis.lean` | 1.517s | 1.433s | 0.020s | 0.029s | 1.518s | 1 | 3.05x | 9.29x |
| `tcs_treap_perm_prob` | `tcs_treap_analysis.lean` | 1.474s | 1.449s | 0.053s | 0.050s | 1.602s | 2 | 2.75x | 7.45x |
| `tcs_wgraph_subset_list` | `tcs_weighted_graph_prefix.lean` | 1.800s | 1.643s | 0.014s | 0.015s | 1.593s | 1 | 3.20x | 10.10x |
| `tcs_wgraph_memconsrw` | `tcs_weighted_graph_prefix.lean` | 1.822s | 1.547s | 0.026s | 0.026s | 1.628s | 1 | 3.36x | 10.08x |
| `tcs_wgraph_subset_comb` | `tcs_weighted_graph_prefix.lean` | 1.764s | 1.598s | 0.041s | 0.039s | 1.708s | 1 | 3.07x | 8.78x |
| `tcs_wgraph_empty` | `tcs_weighted_graph_prefix.lean` | 1.817s | 1.640s | 0.008s | 0.009s | 1.796s | 1 | 3.28x | 10.56x |
| `tcs_wgraph_subgraph` | `tcs_weighted_graph_prefix.lean` | 1.707s | 1.764s | 0.009s | 0.013s | 1.710s | 2 | 2.86x | 9.21x |
| `tcs_wgraph_from_edge_subset_subgraph` | `tcs_weighted_graph_prefix.lean` | 1.771s | 1.665s | 0.011s | 0.012s | 1.719s | 1 | 3.13x | 9.98x |
| `tcs_wgraph_from_edge_subset` | `tcs_weighted_graph_prefix.lean` | 1.740s | 1.687s | 0.152s | 0.171s | 1.900s | 2 | 2.44x | 5.43x |
| `tcs_wgraph_mst` | `tcs_weighted_graph_prefix.lean` | 1.733s | 1.801s | 0.008s | 0.007s | 1.855s | 2 | 2.85x | 9.21x |
| `tcs_wgraph_sym2order` | `tcs_weighted_graph_prefix.lean` | 1.783s | 1.805s | 0.017s | 0.016s | 1.936s | 2 | 2.88x | 9.03x |

### Sequential Same-File Checks

Run policy: 1 measured run per file, sequential execution, 5 declarations per
file. This benchmark models a file-level checking session:

1. check imports/header;
2. for each declaration, check a partial `sorry` version and confirm `sorry` is
   detected without hard errors;
3. check the full declaration and require valid-without-sorry;
4. advance the cached environment only after the full declaration succeeds.

Raw JSON for these rows was written under
`benchmark_results/file-level-local-full-2026-05-13/` and
`benchmark_results/file-level-linux-full-2026-05-13/`. Those directories are
ignored by git, so reruns do not pollute the package history.

| Platform | File | Declarations | Checks | Lake prefix total | Lake full-file total | LeanProbe cached total | LeanProbe fresh total | Speedup vs prefix Lake | Speedup vs full Lake | Speedup vs fresh Probe |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| macOS | `analysis_real.lean` | 5 | 10 | 67.992s | 40.216s | 4.775s | 44.699s | 14.24x | 8.42x | 9.36x |
| macOS | `algebra_order.lean` | 5 | 10 | 46.870s | 48.163s | 4.339s | 40.953s | 10.80x | 11.10x | 9.44x |
| macOS | `sets_functions.lean` | 5 | 10 | 45.421s | 42.237s | 3.916s | 37.797s | 11.60x | 10.79x | 9.65x |
| macOS | `number_theory_nat.lean` | 5 | 10 | 36.474s | 36.648s | 3.789s | 36.736s | 9.63x | 9.67x | 9.70x |
| Linux | `analysis_real.lean` | 5 | 10 | 22.765s | 22.958s | 2.515s | 24.890s | 9.05x | 9.13x | 9.90x |
| Linux | `algebra_order.lean` | 5 | 10 | 23.186s | 23.489s | 2.547s | 25.147s | 9.10x | 9.22x | 9.87x |
| Linux | `sets_functions.lean` | 5 | 10 | 22.679s | 22.567s | 2.384s | 24.195s | 9.51x | 9.47x | 10.15x |
| Linux | `number_theory_nat.lean` | 5 | 10 | 22.593s | 22.829s | 2.301s | 24.086s | 9.82x | 9.92x | 10.47x |

Column guide:

- `Declarations`: number of declarations walked in that file.
- `Checks`: number of scenarios checked. Here each declaration contributes two
  scenarios: a partial declaration containing `sorry`, then the complete
  declaration.
- `Lake prefix total`: total terminal time for `lake env lean` on temp prefix
  files containing header + already accepted prior declarations + current
  scenario.
- `Lake full-file total`: total terminal time for `lake env lean` on temp full
  files where only the current declaration is replaced by the scenario text.
- `LeanProbe cached total`: total time for one LeanProbe/LeanInteract server
  walking the same scenarios while reusing the same-file environment.
- `LeanProbe fresh total`: total time for LeanProbe checks with a fresh
  LeanProbe/LeanInteract server per scenario.
- `Speedup vs prefix/full Lake`: corresponding Lake total divided by
  `LeanProbe cached total`.
- `Speedup vs fresh Probe`: `LeanProbe fresh total / LeanProbe cached total`;
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
PYTHONPATH=src python -m lean_probe.cli benchmark-suite \
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
PYTHONPATH=src python -m lean_probe.cli benchmark-file \
  examples/lean/analysis_real.lean \
  --cwd /path/to/mathlib-lake-project \
  --runs 1 \
  --results-dir benchmark_results/standalone-local-$(date +%F) \
  --pretty
```

To compare another verifier, pass it as a shell command. `{file}` is the temp
full candidate file for the current partial/full scenario:

```bash
PYTHONPATH=src python -m lean_probe.cli benchmark-file \
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
PYTHONPATH=src python -m pytest -q
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

- `ok`: true only when Lean accepts the target without `sorry`;
- `messages`: Lean diagnostics with both chunk-local and file-global positions;
- `tactics`: tactic text, ranges, goals, proof states, and used constants;
- `feedback_lean`: target declaration with inline feedback comments;
- `cache`: header/prior-declaration environment reuse metadata;
- `elapsed_s`: wall-clock time for the check.

## Backend Dependency

[LeanInteract](https://github.com/augustepoiroux/LeanInteract) is LeanProbe's
primary backend dependency. LeanInteract provides the Lean REPL process,
incremental elaboration, command responses, proof states, tactic stepping, and
the low-level interaction API.

LeanProbe builds on that backend with package-level ergonomics for coding
agents: file segmentation, same-file declaration targeting, warm prior
environments, replacement checks, feedback annotation, CLI commands, MCP tools,
and reproducible benchmark harnesses.
