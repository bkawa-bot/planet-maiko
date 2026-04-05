# Cross-Repo Learning Transfer

## Overview

Learnings that transfer across repositories. Three-layer scope classification determines whether a learning is repo-specific or universal, with confidence bonuses for evidence from multiple repos.

## Scope Classification (Three Layers)

1. **Heuristic (free)** -- path references = repo-specific; "Always/Never" without paths = universal; signals from 2+ repos = auto-promote
2. **LLM batch (deferred)** -- batch classification of ambiguous learnings during brain cycle
3. **User toggle (override)** -- manual scope assignment always wins

## Model Changes

New fields on the Learning model:

- `scope_type` -- enum: `repo`, `universal`, `pending`
- `cross_repo_evidence` -- JSON blob tracking which repos contributed evidence
- `cross_repo_key` -- normalized key for matching equivalent learnings across repos

## Aggregation

Two-pass aggregation in `process_signals()`:

1. First pass: group by repo-specific key (existing behavior)
2. Second pass: group by `cross_repo_key` to merge universal learnings across repos

## Confidence Model

- Universal learnings accrue confidence at 0.7x the normal rate
- Bonus of +0.15 when a new repo provides confirming evidence
- Confidence capped at 0.8 unless confirmed by 3+ repos

## Brief Compilation

Reserved slots in the agent brief:

- 2 slots for universal learnings (highest confidence)
- 2 slots for repo-specific learnings
- Remaining slots filled by rank

## Brain Cycle

New phase 4.6: classify pending scopes. Runs heuristic first, queues ambiguous items for LLM batch classification.

## UI

- Universal tab in Knowledge Pool view
- Scope badges on learning rows (repo / universal / pending)
- Cross-repo evidence display showing which repos contributed

## Key Files

- `learning.py` -- model field additions
- `processor.py` -- two-pass aggregation and brief compilation changes
- `scope_classifier.py` -- new module for heuristic + LLM classification
- `learning_api.py` -- API changes for scope queries
- `Knowledge.jsx` -- UI for universal tab, scope badges, evidence display
