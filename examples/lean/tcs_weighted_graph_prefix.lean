-- Benchmark source: CodaBench TCS Proving competition, https://www.codabench.org/competitions/16161/
-- Extracted from the public companion repository https://github.com/epfl-lara/icml-26-lean-challenges, minimum_spanning_tree/Challenges/Minimum_Spanning_Tree/Def_WeightedGraph.lean
-- Prefix extract through Sym2order/ordering setup; later upstream declarations target Lean 4.28 and do not compile unchanged on Lean 4.30.
-- Included as a longer, complete benchmark example.
/-
Copyright (c) 2025 Sorrachai Yingchareonthawornchai· All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Isabel Haas, Pratyai Mazumder, Sorrachai Yingchareonthawornchai
-/

import Mathlib.Tactic
import Mathlib.Combinatorics.SimpleGraph.Basic
import Mathlib.Combinatorics.SimpleGraph.Acyclic
import Mathlib.Combinatorics.SimpleGraph.Finite
import Mathlib.Combinatorics.SimpleGraph.Metric
import Mathlib.Combinatorics.SimpleGraph.Dart

set_option autoImplicit false
set_option tactic.hygienic false
set_option linter.unusedSectionVars false
set_option linter.unusedDecidableInType false

-- small lemmas used multiple times
lemma subsetList {α : Type} [DecidableEq α] {x : α} {xs : List α} {A : Finset α}
  (h: (x::xs).toFinset ⊆ A):
  xs.toFinset ⊆ A := by
  rw [List.toFinset_cons] at h
  rw [Finset.insert_subset_iff] at h
  exact h.2
lemma subsetSet {α : Type} [DecidableEq α] {x : α} {xs : Finset α} {ys : Finset α}
  (h: insert x xs ⊆ ys):
  xs ⊆ ys := by
  rw [Finset.insert_subset_iff] at h
  exact h.2
lemma memconsrw {α : Type} [DecidableEq α] {x y : α} {xs : List α}: x ∈ (y :: xs).toFinset
  ↔ (x = y ∨ x ∈ xs.toFinset)
:= by
  constructor
  · intro h
    aesop
  · intro h
    aesop
lemma subset_comb {α : Type} [DecidableEq α] {x : α} {es : List α} {xs : Finset α} {A : Finset α}
  (h1 : (x::es).toFinset ⊆ A) (h2 : xs ⊆ A):
  insert x xs ⊆ A := by
  apply Finset.insert_subset
  · rw [List.toFinset_cons] at h1
    rw [Finset.insert_subset_iff] at h1
    exact h1.1
  · exact h2

-- == WeightedGraph definition built on Mathlib Simplegraph ==

@[ext]
structure WeightedGraph (V : Type) extends SimpleGraph V where
  weight : V → V → ℕ
  weight_symm : ∀ {u v}, weight u v = weight v u
  weight_zero_iff_not_adj : ∀ {u v}, weight u v = 0 ↔ ¬ toSimpleGraph.Adj u v
instance {V} : Coe (WeightedGraph V) (SimpleGraph V) :=
  ⟨WeightedGraph.toSimpleGraph⟩
instance {V : Type} [DecidableEq V] (G : WeightedGraph V) :
    DecidableRel G.Adj :=
by
  intro u v
  have h := G.weight_zero_iff_not_adj (u := u) (v := v)
  have : Decidable (¬ G.weight u v = 0) := by
    exact instDecidableNot
  simpa [G.weight_zero_iff_not_adj] using this

noncomputable instance {V : Type} [DecidableEq V] (G : SimpleGraph V) :
    DecidableRel G.Adj :=
by
  intro u v
  exact Classical.propDecidable (G.Adj u v)

variable {V : Type} [Fintype V] [DecidableEq V] [LinearOrder V]
variable {G H : WeightedGraph V}
namespace WeightedGraph

universe u


-- == WeightedGraph methods and theorems ==
-- inductive Walk {G: WeightedGraph V} : V → V → Type u
--   | nil {u : V} : Walk u u
--   | cons {u v w : V} (h : G.Adj u v) (p : G.Walk v w) : G.Walk u w
--   deriving DecidableEq
abbrev Walk :=
  SimpleGraph.Walk (G : SimpleGraph V)

namespace Walk

abbrev IsCycle {u : V} (p : G.Walk u u): Prop :=
  SimpleGraph.Walk.IsCycle p
end Walk

abbrev IsAcyclic: Prop :=
  SimpleGraph.IsAcyclic (G : SimpleGraph V)
abbrev Connected: Prop :=
  SimpleGraph.Connected (G : SimpleGraph V)
abbrev IsTree: Prop :=
  SimpleGraph.IsTree (G : SimpleGraph V)
abbrev connected_iff :=
  SimpleGraph.connected_iff (G : SimpleGraph V)

-- Empty weighted graph
def emptyGraph : WeightedGraph V where
  toSimpleGraph := SimpleGraph.emptyGraph V
  weight := fun _ _ ↦ 0
  weight_symm := by intros; rfl
  weight_zero_iff_not_adj := by
    intro u v
    exact (iff_true_right fun a ↦ a).mpr rfl
@[simps]
instance : Inhabited (WeightedGraph V) := ⟨emptyGraph⟩


-- Edge Set
abbrev edgeFinset: Finset (Sym2 V) :=
  SimpleGraph.edgeFinset (G : SimpleGraph V)

-- Subgraph definition
def IsSubgraph (H G : WeightedGraph V) : Prop :=
  SimpleGraph.IsSubgraph (H : SimpleGraph V) (G : SimpleGraph V) ∧
  -- additionally, weights have to be the same
  (∀ u v, H.Adj u v → H.weight u v = G.weight u v)



--  SimpleGraph.FromEdgeSet returns a subgraph of sG
lemma SimpleGraph.from_edge_subset_is_subgraph (sG : WeightedGraph V) (s: Set (Sym2 V))
  (hset: s ⊆ sG.edgeFinset):
   SimpleGraph.fromEdgeSet s ≤ sG.toSimpleGraph :=
  by
  apply SimpleGraph.fromEdgeSet_mono at hset
  rw [SimpleGraph.coe_edgeFinset, SimpleGraph.fromEdgeSet_edgeSet] at hset
  exact hset


-- Creates graph from edgeSubset, sG provides weight function
def FromEdgeSubset (sG : WeightedGraph V) (s: Finset (Sym2 V))
  (hset: s ⊆ sG.edgeFinset): WeightedGraph V :=
  let uG := SimpleGraph.fromEdgeSet s
  { toSimpleGraph := uG
    weight :=  fun x y => if h : uG.Adj x y then sG.weight x y else 0
    weight_symm := by
      intro u v
      have h := weight_symm sG (u := u) (v := v)
      split_ifs
      · exact h
      · expose_names
        apply SimpleGraph.adj_symm at h_1
        contradiction
      · expose_names
        apply SimpleGraph.adj_symm at h_2
        contradiction
      · rfl
    weight_zero_iff_not_adj := by
      intro u v
      have h := weight_zero_iff_not_adj sG (u := u) (v := v)
      split_ifs
      · expose_names
        apply SimpleGraph.from_edge_subset_is_subgraph at hset
        rw [SimpleGraph.le_iff_adj] at hset
        have h2 := hset u v
        aesop
      · aesop
    }



-- == Definition of Minimum Spanning Tree ==
def IsSpanningTree (H G : WeightedGraph V) : Prop :=
  H.IsSubgraph G ∧ H.IsTree
-- weight sum of graph
noncomputable def weightSum  (G : WeightedGraph V) : ℕ :=
  ∑ e ∈ G.edgeFinset, G.weight e.out.1 e.out.2
def IsMST (T G: WeightedGraph V) : Prop :=
  T.IsSpanningTree G ∧
  (∀ T' : WeightedGraph V, T'.IsSpanningTree G → T.weightSum ≤ T'.weightSum)


end  WeightedGraph


-- == Definition of a linear order on edge list ==

noncomputable def WeightedGraph.EdgeList (G : WeightedGraph V) : List (Sym2 V) :=
   G.edgeFinset.toList
def Sym2order
  (a b : Sym2 V)  :=
  let (x, y) := a.out
  let (s, t) := b.out
  let (mina, maxa) := (if x ≤ y then (x,y) else (y,x))
  let (minb, maxb) := (if s ≤ t then (s,t) else (t,s))
  mina < minb ∨ (mina = minb ∧ maxa <= maxb)
instance : IsTrans (Sym2 V) (Sym2order) := by
  refine { trans := ?_ }
  unfold Sym2order
  grind
