# Cost-Aware Model Routing

## Overview

Intelligent model selection per task type, routing work to the cheapest capable model. Estimated 60-70% cost reduction on routine brain cycle work.

## Model Tiers

| Tier | Model | Use Cases |
|------|-------|-----------|
| Light | Haiku | Triage, classification, scene generation |
| Standard | Sonnet | Skills, project planning |
| Heavy | Opus | Coding agents, tournaments, training |

## Routing Mechanism

- New `routing.py` module with `resolve_model(task_type)` that looks up config rules
- Add `--model` flag to `claude --print` calls (already supported by CLI)
- Switch to `--output-format json` to capture `total_cost_usd` and token counts from responses

## Usage Tracking

- New `ModelUsage` DB table tracks every invocation (model, tokens, cost, task type, timestamp)
- API endpoints for visibility:
  - `GET /api/costs/summary` -- aggregate spend by model and task type
  - `GET /api/costs/savings` -- estimated savings vs all-Opus baseline
  - `GET /api/costs/history` -- time-series cost data

## Configuration

Config section `routing.rules` maps task types to models with sensible defaults. Settings UI section for adjusting rules without editing config files.

## Migration

18 call sites need the model parameter threaded through.

## Key Files

- `claude_code.py` -- add model param to invocation
- `base.py` -- thread model through base skill calls
- `brain_session.py` -- route brain cycle phases to appropriate tiers
- `config.py` -- routing rules configuration
- `routing.py` -- new module for model resolution logic
- `model_usage.py` -- new module for usage tracking model and queries
