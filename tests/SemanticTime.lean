-- Private - personal, financial, and mental-health data
import Std

set_option maxRecDepth 4096

namespace SemanticTime

abbrev Gaussian := Int × Int

def mul (z w : Gaussian) : Gaussian :=
  (z.1 * w.1 - z.2 * w.2, z.1 * w.2 + z.2 * w.1)

def add (z w : Gaussian) : Gaussian := (z.1 + w.1, z.2 + w.2)

-- Each output row is a linear combination of rows of the right operand.
def matrixProduct (width : Nat) (a b : List (List Gaussian)) : List (List Gaussian) :=
  a.map fun row =>
    (row.zip b).foldl (fun acc (weight, rhs) =>
      acc.zipWith add (rhs.map (mul weight))) (List.replicate width (0, 0))

theorem mul_add (u v w : Gaussian) : mul u (add v w) = add (mul u v) (mul u w) := by
  apply Prod.ext <;> simp [mul, add] <;> grind

theorem mul_assoc (u v w : Gaussian) : mul (mul u v) w = mul u (mul v w) := by
  apply Prod.ext <;> simp [mul] <;> grind

def horner (x : Gaussian) : List Gaussian → Gaussian
  | [] => (0, 0)
  | c :: cs => add c (mul x (horner x cs))

def powerSum (x factor : Gaussian) : List Gaussian → Gaussian
  | [] => (0, 0)
  | c :: cs => add (mul factor c) (powerSum x (mul factor x) cs)

theorem powerSum_eq_horner (x factor : Gaussian) (cs : List Gaussian) :
    powerSum x factor cs = mul factor (horner x cs) := by
  induction cs generalizing factor with
  | nil => simp [powerSum, horner, mul]
  | cons c cs ih => rw [powerSum, horner, ih, mul_add, mul_assoc]

-- Equal degrees plus d+1 distinct probes detect a wrong coefficient over Gaussian integers.
def checkShift (cs shifted : List Gaussian) (point : Gaussian) : Bool :=
  cs.length == shifted.length && (List.range (max 1 cs.length)).all (fun k =>
    let h : Gaussian := (k, 1)
    powerSum h (1, 0) shifted == powerSum (add point h) (1, 0) cs)

def normSq (z : Gaussian) : Int := z.1 * z.1 + z.2 * z.2

theorem norm_mul (z w : Gaussian) :
    normSq (mul z w) = normSq z * normSq w := by
  simp [normSq, mul]
  grind

def quarter (z : Gaussian) : Gaussian := (-z.2, z.1)

theorem norm_quarter (z : Gaussian) : normSq (quarter z) = normSq z := by
  simp [normSq, quarter, Int.add_comm]
  grind

theorem four_quarters (z : Gaussian) :
    quarter (quarter (quarter (quarter z))) = z := by
  simp [quarter]

-- Value of a degree-n polynomial by nested multiplication at the supplied point.
def polynomial (x : Gaussian) : Nat → Gaussian
  | 0 => (1, 0)
  | n + 1 =>
    let p := mul (polynomial x n) x
    (p.1 + (n : Int) + 1, p.2 - (n : Int))

-- Twice the overlap of two equal-amplitude labels after one acquires i^ticks.
def twiceOverlap (ticks : Int) : Gaussian :=
  match ticks % 4 with
  | 0 => (2, 0)
  | 1 => (1, 1)
  | 2 => (0, 0)
  | _ => (1, -1)

end SemanticTime
