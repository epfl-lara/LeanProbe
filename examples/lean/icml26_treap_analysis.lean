-- Source: https://github.com/epfl-lara/icml-26-lean-challenges, treap/Challenges/Treap/Def_Analysis.lean
-- Included as a longer, complete benchmark example.
/-
Copyright (c) 2025 Sorrachai Yingchareonthawornchai. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Carlo Collodel, Sorrachai Yingchareonthawornchai
-/

import Mathlib.Probability.ProbabilityMassFunction.Basic
import Mathlib.Probability.ProbabilityMassFunction.Constructions
import Mathlib.Probability.ProbabilityMassFunction.Integrals
import Mathlib.MeasureTheory.Integral.Bochner.Basic
import Mathlib.NumberTheory.Harmonic.Bounds
import Mathlib.Data.Real.Basic

open MeasureTheory ProbabilityTheory ENNReal BigOperators

-- Define the Sample Space (Ω)
variable {n : ℕ}

-- Use a local notation since abbrev would require n as a parameter
local notation "Ω" => Equiv.Perm (Fin n)

-- Define the Measurable Space
instance : MeasurableSpace Ω := ⊤
instance : MeasurableSingletonClass Ω := ⟨by simp⟩

-- Define the Probability Measure (P) manually (this is just a uniform over all permutations)

-- Define a function that assigns 1/n! to every outcome.
noncomputable def perm_prob (_ : Ω) : ℝ≥0∞ := 1 / (Fintype.card Ω)

-- Prove that the sum of probabilities is 1.
theorem uniform_prob_sum_one {α : Type*} [Fintype α] [Nonempty α] (ω : α → ℝ≥0∞)
    (h : ∀ a : α, ω a = 1 / (Fintype.card α)) : ∑ a : α, ω a = 1 := by
  simp only [h]
  simp only [Finset.sum_const, Finset.card_univ, nsmul_eq_mul, one_div]
  rw [ENNReal.mul_inv_cancel]
  · simp only [ne_eq, Nat.cast_eq_zero, Fintype.card_ne_zero, not_false_eq_true]
  · simp only [ne_eq, natCast_ne_top, not_false_eq_true]

theorem perm_prob_sum_one : ∑ ω : Ω, perm_prob ω = 1 :=
  uniform_prob_sum_one perm_prob (congrFun rfl)

-- Create the PMF object using the function and the proof
noncomputable def permPMF : PMF Ω := PMF.ofFintype perm_prob perm_prob_sum_one

-- Convert the PMF to a Measure (P)
noncomputable def P : Measure Ω := permPMF.toMeasure

-- We need this to prove that isAncestor integral will be Integrable
noncomputable instance (n : ℕ) : IsProbabilityMeasure (P (n := n)) :=
  PMF.toMeasure.isProbabilityMeasure permPMF
noncomputable instance (n : ℕ) : IsFiniteMeasure (P (n := n)) := by infer_instance

-- Define the Random Variable isAncestor
-- Actually, isAncestor will be a function of j, k that returns a random variable
-- The result is 1 if perm(j) > perm([j, k]), else 0
noncomputable def isAncestor (j k : Fin n) : Ω → ℝ :=
  fun perm =>
    if ∀ i ∈ Finset.Icc (min j k) (max j k), i ≠ j → perm j > perm i then 1 else 0

-- Define a new class of random variables depth, which is the sum of all ancestors
noncomputable def depth (k : Fin n) : Ω → ℝ :=
  ∑ j : Fin n, isAncestor j k
