# async-multi-agent-wealth-planner

Project 29: Asynchronous AWS SQS, Aurora PostgreSQL, and S3 Vectors multi-agent financial planner with job idempotency, per-specialist stage tracking, and 100% chart sum validation.

## Architecture

```
Clerk JWT -> FastAPI / Job API -> AWS SQS (at-least-once)
                                      |
                                      v
                         AsyncWealthPlannerEngine
         +----------------+----------------+----------------+
         |                |                |                |
         v                v                v                v
      Tagger           Reporter         Charter        Retirement
         |                |                |                |
         +----------------+----------------+----------------+
                                      |
                                      v
                 Aurora PostgreSQL (Job & Stage State) + S3 Vectors
```

- **`wealth_planner/models.py`**: Strict Pydantic v2 data contracts for positions, chart allocations, specialist stage statuses, and job records.
- **`wealth_planner/engine.py`**: `AsyncWealthPlannerEngine` implementing Clerk user scoping, SQS idempotency locks, per-stage execution tracking, and deterministic 100% chart allocation validation.
- **`wealth_planner/cli.py`**: Runnable local demo entrypoint exercising the full multi-agent pipeline.
- **`tests/test_engine.py`**: Pytest suite verifying cross-user 403 isolation, duplicate SQS delivery idempotency, partial-failure `PARTIAL` status handling, and 100% chart sum enforcement.

## 6-Stage Workflow

1. **Clerk User-Scoped Ingestion**: Validates `clerk_user_id` and `PortfolioPosition` payloads before job creation.
2. **Idempotent SQS Enqueue**: Deduplicates submissions on `(clerk_user_id, idempotency_key)` and dispatches to SQS.
3. **Tagger Specialist**: Classifies holdings by asset class, sector, and unrealized gain/loss.
4. **Reporter Specialist**: Synthesizes portfolio totals and generates structured Markdown analysis.
5. **Charter Specialist**: Computes asset-class allocations and enforces deterministic `100.0%` weight summation.
6. **Retirement Specialist & Aggregation**: Projects long-horizon portfolio value and computes final job status (`COMPLETE`, `PARTIAL`, or `FAILED`).

## Data Contract

- **`PortfolioPosition`**: `symbol`, `asset_class`, `shares`, `market_value_usd`, `cost_basis_usd`, `sector`, `account_type`.
- **`ChartAllocation`**: `category`, `weight_pct` (0.0-100.0), `amount_usd`.
- **`SpecialistStageStatus`**: `stage` (`tagger | reporter | charter | retirement`), `status` (`PENDING | RUNNING | COMPLETE | FAILED`), `attempts`, `output`, `error`, `updated_at`.
- **`AnalysisJobRecord`**: `job_id`, `clerk_user_id`, `idempotency_key`, `status` (`QUEUED | RUNNING | COMPLETE | PARTIAL | FAILED`), `positions`, `stages`, `chart_allocations`, `report_markdown`, `retirement_projection`.

## Production Controls

- **Cross-User Isolation (403)**: `get_job(job_id, clerk_user_id)` raises `PortfolioAccessError` (`status_code = 403`) on any user mismatch.
- **SQS At-Least-Once Idempotency**: Per-job `asyncio.Lock` and stage-level `COMPLETE` checks prevent duplicate specialist execution on redelivered SQS messages.
- **Partial Failure Safety**: If any specialist (e.g., `Charter`) fails, its stage is marked `FAILED` and the job transitions to `PARTIAL`—never `COMPLETE`.
- **Deterministic 100% Chart Sum**: `validate_chart_allocations` rejects allocation sets whose `weight_pct` sum deviates from `100.0%`.

## Local Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -v
python -m wealth_planner.cli
```

## Attribution

Inspired by and adapted with pinned attribution from [`ed-donner/alex`](https://github.com/ed-donner/alex).
 job idempotency, per-specialist stage tracking, and 100% chart sum validation.
