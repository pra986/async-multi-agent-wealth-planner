# Architecture — Async Multi-Agent Wealth Planner

## System Overview

The **Async Multi-Agent Wealth Planner** (Project 29 / D4 Production Track) is an event-driven financial planning platform built on **AWS SQS**, **Aurora PostgreSQL**, **S3 Vectors**, **AWS Bedrock**, and **Clerk** authentication.

```
Clerk JWT -> API Gateway / FastAPI -> Aurora PostgreSQL (Job + Lock)
                                         |
                                         v
                                    AWS SQS Queue
                                         |
                                         v
                             Planner Orchestrator Worker
                 +-----------+-----------+-----------+-----------+
                 |           |           |           |           |
                 v           v           v           v           v
              Tagger      Reporter    Charter   Retirement   S3 Vectors
             (Holdings)  (Narrative) (Charts)  (Monte Carlo)  (RAG)
```

## 6-Stage Asynchronous Workflow

1. **Authenticated Submission**: Requests carry a Clerk `user_id`. Portfolio records and analysis jobs are strictly scoped to `user_id`. Cross-user polling returns HTTP `403 Forbidden`.
2. **Idempotent Enqueue & Lock Acquisition**: Submitting or consuming a job acquires an idempotency lock keyed by `(job_id, sqs_message_id)` in Aurora PostgreSQL. Duplicate SQS deliveries do not re-execute completed stages.
3. **Stage 1 — Tagger Specialist**: Classifies portfolio positions by asset class, sector, and geographic exposure.
4. **Stage 2 — Reporter Specialist**: Queries S3 Vectors for relevant market research and synthesizes a structured narrative report via AWS Bedrock.
5. **Stage 3 — Charter Specialist**: Generates portfolio allocation charts and enforces deterministic **100% chart sum validation** (weights must sum to `100.0%` within `0.01%` tolerance).
6. **Stage 4 — Retirement Specialist**: Projects retirement readiness, withdrawal sustainability, and target gap analysis.

## Production Controls

- **At-Least-Once SQS Idempotency**: Already-completed specialist stages are skipped on redelivery; only `PENDING` or `FAILED` stages run.
- **Per-Specialist Stage Tracking**: Each stage (`tagger`, `reporter`, `charter`, `retirement`) records its own `SpecialistStageStatus` (`PENDING`, `RUNNING`, `COMPLETED`, `FAILED`).
- **Partial Failure Isolation**: If any specialist fails (e.g., `charter` fails validation or upstream Bedrock timeout), the overall job status transitions to `PARTIAL`—never `COMPLETE`.
- **Tenant Isolation**: Every job read (`get_job`) verifies `job.user_id == requesting_user_id`, raising `PermissionError` (mapped to HTTP 403) on mismatch.
