# Experiment Tracking

## Core Goal

Produce validated research data within 6 months and complete a CHI/CSCW-level paper.

## Current Phase

Week 1 - Data collection closed loop with Alpha/Beta chat modes, drift
calibration, and external blind-test collection.

## Metrics Status Board

| Metric | Validation Target | Current Status |
| --- | --- | --- |
| M1-M2 | Drift detection accuracy / counterfactual consistency | Zero-shot drift judge endpoint and `DRIFT_DETECTED` events wired; manual calibration UI available |
| M3-M4 | IPIP-120 & PVQ-21 personality baseline collection progress | 0 |
| M5 | Core identity retention | `mode_alpha` full IACL vs `mode_beta` static-prompt baseline can now be compared by session |
| M6 | Friend Turing Test progress | Public `/evaluations/{agent_id}` blind-test page and `Evaluation` table wired |

## Current Instrumentation

- Chat rows now carry `branch_id`, `session_id`, and `experiment_mode`.
- `mode_alpha` is the full IACL / active-memory condition.
- `mode_beta` is the static prompt baseline with tools, RAG, history, and memory updates disabled.
- `/api/chat/{agent_id}/check-drift` evaluates recent Agent replies against the identity core and records `DRIFT_DETECTED` when needed.
- `/api/evaluations/blind-test/{agent_id}` samples chat snippets for external raters.
- `/api/evaluations/blind-test/{agent_id}/submit` stores relation, 1-5 authenticity score, optional feedback, and sampled chat ids.

## Immediate Data-Quality Checks

- Confirm each participant has separate `session_id` values for independent conversations.
- Confirm Alpha/Beta labels are balanced enough for comparison before analysis.
- Confirm external raters understand that 1 means "not like the participant" and 5 means "very like the participant".
- Export or query `Evaluation` rows together with sampled `ChatLog` ids before reporting M6.
