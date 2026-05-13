# LeanProbe Benchmark Details

This file holds the detailed per-target benchmark rows for the May 13, 2026
README results. The README keeps the grouped summary tables and methodology.

Run policy for these repeated-target rows: 1 measured run per target, 0
benchmark warmups, warm Lake caches from prior example validation, feedback
enabled, and fresh-server baseline enabled. Prepare time is shown separately
and included in break-even and amortized speedups.

Column guide:

- `Lake full-file check`: one `lake env lean` run on a temp full file for this
  target.
- `Probe prepare env`: one `lean_probe_prepare` run for the environment before
  this target.
- `Probe cached check`: one warm `lean_probe_check` against the prepared
  environment.
- `Probe cached feedback`: one warm `lean_probe_feedback` against the prepared
  environment.
- `Probe fresh check`: one `lean_probe_check` with a fresh
  LeanProbe/LeanInteract server and no cache reuse.
- `Attempts to beat Lake`: minimum number of repeated target checks needed for
  `Probe prepare env + n * Probe cached check` to be faster than
  `n * Lake full-file check`.
- `Amortized speedup, 3/10 attempts`: `(n * Lake full-file check) /
  (Probe prepare env + n * Probe cached check)`. Values below `1.0x` mean the
  prepare cost has not yet paid off.

## Compact Examples: macOS Per-Target Detail

| Case label | File | Lake full-file check | Probe prepare env | Probe cached check | Probe cached feedback | Probe fresh check | Attempts to beat Lake | Amortized speedup, 3 attempts | Amortized speedup, 10 attempts |
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
in this run. Its prepare time is therefore much higher, and it needs four check
attempts to break even.

## TCS Challenge Examples: macOS Per-Target Detail

| Case label | File | Lake full-file check | Probe prepare env | Probe cached check | Probe cached feedback | Probe fresh check | Attempts to beat Lake | Amortized speedup, 3 attempts | Amortized speedup, 10 attempts |
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

## TCS Challenge Examples: Linux Per-Target Detail

| Case label | File | Lake full-file check | Probe prepare env | Probe cached check | Probe cached feedback | Probe fresh check | Attempts to beat Lake | Amortized speedup, 3 attempts | Amortized speedup, 10 attempts |
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
