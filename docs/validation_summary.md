# Validation Summary

## Purpose

The validation layer tests whether inferred delay-propagation relationships are stronger than nearby local alternatives and whether the findings are robust to threshold choices.

## Control comparison results

- Cases evaluated: **48**

- SUPPORTS_CASE: **32**
- DOES_NOT_SUPPORT: **7**
- NO_CONTROL_AVAILABLE: **5**
- PARTIAL_SUPPORT: **4**

## Threshold sensitivity

The threshold-sensitivity table records how propagation outputs change under alternative time-gap and delay-threshold assumptions.

- Sensitivity settings tested: **9**

## Negative/control stress tests

The negative-control tests compare the normal same-direction propagation logic against less plausible cases such as opposite-direction or larger-gap interactions.

- Negative/control settings tested: **3**

## Interpretation

The validation outputs do not prove operational causality, but they provide evidence that the inferred propagation cases are stronger than simple co-location or random local delay patterns.
