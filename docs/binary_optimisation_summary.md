# Binary Optimisation Summary

## Purpose

The binary optimisation layer selects a subset of candidate recovery interventions subject to budget and maximum-intervention constraints.

Each candidate intervention is represented by a binary decision variable:

```text
x_i = 1 if intervention i is selected
x_i = 0 otherwise
```

The objective is to maximise estimated avoided downstream delay.

## Optimisation result

- Solver used: **scipy_milp**
- Total candidates: **200**
- Selected interventions: **3**
- Total budget: **8.0**
- Used budget: **8.00**
- Estimated total avoided impact: **406.06**

## Selected BIP/MILP interventions

- location: EDINBURGH (benefit: 244.64, cost: 4.00)
- location: POLEGATE (benefit: 93.05, cost: 2.50)
- location: MONKTONHALL JN (benefit: 68.37, cost: 1.50)

## Interpretation

For small candidate sets the script can solve the binary optimisation by exact enumeration. For larger candidate sets it can switch to a MILP approach if the required solver is available.

The optimisation is conceptually meaningful as a recovery-prioritisation model, but its costs are heuristic and should not be interpreted as real Network Rail intervention costs.
