# Project 1 Final Summary: Railway Delay Propagation and Recovery Optimisation

## Overview

This project builds an end-to-end railway delay propagation pipeline using Network Rail timetable and train movement data.

The completed project includes:

- timetable and movement-data ingestion,
- SQLite database construction,
- train-level delay analysis,
- location-level delay hotspot analysis,
- candidate train-to-train propagation detection,
- causal-style propagation network construction,
- validation using local controls and robustness checks,
- visualisation of propagation patterns,
- recovery intervention prioritisation,
- and a small binary optimisation layer for constrained recovery decision support.

## Key project metrics

- Movement events loaded: **402270**
- Directed causal propagation edges: **1841**
- Trains in causal propagation network: **1269**
- Causal propagation locations: **596**

## Strongest causal propagation edge

- Source train: **045R552K06**
- Affected train: **041M87MK06**
- Sample location: **EDINBURGH**
- Edge weight: **110.70**

## Strongest causal propagation location

- Location: **MANCHESTER PICCADILLY**
- Location causal score: **190.30**

## Most central train in causal network

- Train ID: **041M87MK06**
- Node role: **INTERMEDIATE**
- Total network weight: **467.74**

## Interpretation

The project infers likely knock-on delay pathways by combining temporal ordering, shared locations, delay severity, post-interaction worsening, and network structure.

The final optimisation layer uses this inferred propagation network to prioritise recovery interventions under limited resources.

## Limitations

The project uses publicly available movement and timetable data. It does not directly observe signalling block occupation, route setting, dispatcher decisions, crew diagrams, rolling-stock diagrams, or exact platform conflicts.

Therefore, the causal and optimisation outputs should be interpreted as decision-support hypotheses rather than verified operational instructions.
