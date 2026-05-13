-- Benchmark source: CodaBench TCS Proving competition, https://www.codabench.org/competitions/16161/
-- Extracted from the public companion repository https://github.com/epfl-lara/icml-26-lean-challenges, binary_heap/Challenges/BinaryHeap_Dijkstra/Def_BinaryHeap.lean
-- Included as a longer, complete benchmark example.
/-
Copyright (c) 2025 Sorrachai Yingchareonthawornchai. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Sonja Joost, Josefine Lindmar, Sorrachai Yingchareonthawornchai
-/

import Mathlib.Tactic

set_option autoImplicit true

inductive BinaryTree (α : Type u)
  | leaf : BinaryTree α
  | node : BinaryTree α → α → BinaryTree α → BinaryTree α

namespace BinaryTree

def is_min_heap : BinaryTree α → (dist : α → ENat) → Prop
| leaf, _ => true
| node l v r, f => match l, r with
      | leaf, leaf => true
      | node _ lv _, leaf =>
          f v <= f lv ∧ is_min_heap l f
      | leaf, node _ rv _ =>
          f v <= f rv ∧ is_min_heap r f
      | node _ lv _, node _ rv _ =>
          f v <= f lv ∧ is_min_heap l f ∧ f v <= f rv ∧ is_min_heap r f

def heapify (bt: BinaryTree α) (f: α → ENat): BinaryTree α := match bt with
| leaf => bt
| node l v r => match l, r with
    | leaf, leaf => bt
    | leaf, node rl rv rr => if f rv < f v then node l rv (heapify (node rl v rr) f) else bt
    | node ll lv lr, leaf => if f lv < f v then node (heapify (node ll v lr) f) lv r else bt
    | node ll lv lr, node rl rv rr =>  if f lv <= f rv then
                                          if f v <= f lv then bt
                                          else node (heapify (node ll v lr) f) lv r
                                        else
                                          if f v <= f rv then bt
                                          else node l rv (heapify (node rl v rr) f)

def get_last: BinaryTree α → Option α ×  BinaryTree α
| leaf => (none, leaf)
| node l v r => match l, r with
    | leaf, leaf => (some v, leaf)
    | leaf, _ => let (val, tree) := (get_last r)
      (val, node l v tree)
    | _, _ => let (val, tree) := (get_last l)
      (val, node tree v r)

def extract_min (bt: BinaryTree α) (f: α → ENat): (Option α × BinaryTree α):=
let (lastNode, treeWithoutLast) := get_last bt
match lastNode with
| none => (none, leaf)
| some v' => match treeWithoutLast with
  | leaf => (some v', leaf)
  | node l v r => (some v, heapify (node l v' r) f)

def heap_min:  BinaryTree α → (α → ENat) → ENat
| leaf, _ => ⊤
| node l v r, f => match l, r with
    | leaf, leaf => (f v)
    | leaf, node _ rv _ => if f v <= f rv then f v else f rv
    | node _ lv _, leaf => if f v <= f lv then f v else f lv
    | node _ lv _, node _ rv _ =>  if f lv <= f rv then
                                     if f v <= f lv then f v
                                     else f lv
                                    else
                                      if f v <= f rv then f v
                                      else f rv


def root_is_min_of_children: (BinaryTree α) → (α → ENat) →  Prop
| leaf, _ => true
| node l v r, f => match l, r with
    | leaf, leaf => true
    | leaf, node _ rv _ => f v <= f rv
    | node _ lv _, leaf => f v <= f lv
    | node _ lv _, node _ rv _ =>  f v <= f lv ∧ f v <= f rv


def left_and_right_are_min_heap: (BinaryTree α) →  (f: α → ENat) →  Prop
| leaf, _ => true
| node l _ r, f => is_min_heap l f ∧ is_min_heap r f

def contains : (BinaryTree α) → α → Prop
| leaf, _ => false
| node l v r, v' => v = v' ∨ contains l v' ∨ contains r v'


def containsb [DecidableEq α] : (BinaryTree α) → α → Bool
| leaf, _ => false
| node l v r, v' => (v = v') ∨ containsb l v' ∨ containsb r v'

def insert (bt : BinaryTree α) (v : α) (f : α → ENat) : BinaryTree α :=
  match bt with
  | leaf =>
      node leaf v leaf
  | node l x r =>
      if f v ≤ f x then
        node (insert l x f) v r
      else
        node (insert l v f) x r

def merge (bt1 bt2 : BinaryTree α) (f : α → ENat) : BinaryTree α :=
  match bt1, bt2 with
  | leaf, t => t
  | t, leaf => t
  | node l1 v1 r1, node l2 v2 r2 =>
      if f v1 ≤ f v2 then
        node (merge l1 (node l2 v2 r2) f) v1 r1
      else
        node (merge (node l1 v1 r1) l2 f) v2 r2

def remove (bt : BinaryTree α) (x : α) (f : α → ENat)
  [DecidableEq α] : BinaryTree α :=
  match bt with
  | leaf => leaf
  | node l v r =>
      if x = v then
        merge l r f
      else
        node (remove l x f) v (remove r x f)

def decrease_priority [DecidableEq α] (bt : BinaryTree α) (v : α) (f : α → ENat): BinaryTree α :=
  if containsb bt v then insert (remove bt v f) v f else bt

def is_empty_tree [DecidableEq α] (bt: BinaryTree α): Bool :=  match bt with
| leaf => true
| node _ _ _ => false


def subTree (sub sup: BinaryTree α):= ∀ v, contains sub v → contains sup v

def size: (BinaryTree α) →  Nat
| leaf => 0
| node l _ r => 1 + size l + size r

end BinaryTree

-- BinaryHeap definition and basic operations "The Binary Heap API" used in Dijkstra
open BinaryTree

structure BinaryHeap (α : Type u) [DecidableEq α] where
  tree : BinaryTree α

namespace BinaryHeap

def empty [DecidableEq α] : BinaryHeap α := { tree := BinaryTree.leaf }

def isEmpty [DecidableEq α] (h : BinaryHeap α) : Bool := match h.tree with
  | leaf => true
  | node _ _ _ => false

def add {α : Type u} [DecidableEq α] (h : BinaryHeap α) (v : α) (priority : α → ENat) : BinaryHeap α :=
  { tree := h.tree.insert v priority }

def sizeOf {α : Type u} [DecidableEq α] (h : BinaryHeap α) : Nat := h.tree.size

def decrease_priority [DecidableEq α] (h : BinaryHeap α) (v : α) (prio : α → ENat) : BinaryHeap α :=
  { tree := BinaryTree.decrease_priority h.tree v prio }

end BinaryHeap
