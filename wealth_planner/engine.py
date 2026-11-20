from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from uuid import uuid4

from wealth_planner.models import (
    AnalysisJobRecord,
    ChartAllocation,
    JobStatus,
    PortfolioPosition,
    SpecialistName,
    StageState,
)


class PortfolioAccessError(PermissionError):
    """Raised when a Clerk user attempts to access another user's job."""

    status_code: int = 403

    def __init__(self, message: str = "Forbidden: cross-user portfolio access denied") -> None:
        super().__init__(message)
        self.status_code = 403


class ChartAllocationValidationError(ValueError):
    """Raised when chart allocation percentages do not deterministically sum to 100%."""


SpecialistCallable = Callable[[AnalysisJobRecord], Awaitable[Dict[str, Any]]]


class AsyncWealthPlannerEngine:
    """Async multi-agent wealth planner with Clerk scoping, SQS idempotency, and stage tracking."""

    def __init__(
        self,
        specialists: Optional[Dict[SpecialistName, SpecialistCallable]] = None,
    ) -> None:
        self._jobs: Dict[str, AnalysisJobRecord] = {}
        self._idempotency_index: Dict[Tuple[str, str], str] = {}
        self._job_locks: Dict[str, asyncio.Lock] = {}
        self._processed_sqs_messages: Dict[str, set[str]] = {}
        self._specialists: Dict[SpecialistName, SpecialistCallable] = {
            SpecialistName.TAGGER: self._default_tagger,
            SpecialistName.REPORTER: self._default_reporter,
            SpecialistName.CHARTER: self._default_charter,
            SpecialistName.RETIREMENT: self._default_retirement,
        }
        if specialists:
            self._specialists.update(specialists)

    @staticmethod
    def validate_chart_allocations(allocations: List[ChartAllocation]) -> List[ChartAllocation]:
        if not allocations:
            raise ChartAllocationValidationError("Chart allocations cannot be empty.")
        total_weight = round(sum(item.weight_pct for item in allocations), 4)
        if abs(total_weight - 100.0) > 1e-4:
            raise ChartAllocationValidationError(
                f"Chart allocations must sum to 100.0%, got {total_weight}%."
            )
        return allocations

    def submit_job(
        self,
        clerk_user_id: str,
        positions: List[PortfolioPosition],
        idempotency_key: str,
    ) -> AnalysisJobRecord:
        if not clerk_user_id.strip():
            raise ValueError("clerk_user_id is required.")
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required.")

        lookup_key = (clerk_user_id, idempotency_key)
        if lookup_key in self._idempotency_index:
            existing_id = self._idempotency_index[lookup_key]
            return self._jobs[existing_id]

        job_id = f"job_{uuid4().hex[:12]}"
        record = AnalysisJobRecord(
            job_id=job_id,
            clerk_user_id=clerk_user_id,
            idempotency_key=idempotency_key,
            positions=positions,
        )
        self._jobs[job_id] = record
        self._idempotency_index[lookup_key] = job_id
        self._job_locks[job_id] = asyncio.Lock()
        self._processed_sqs_messages[job_id] = set()
        return record

    def get_job(self, job_id: str, clerk_user_id: str) -> AnalysisJobRecord:
        if job_id not in self._jobs:
            raise KeyError(f"Job '{job_id}' not found.")
        job = self._jobs[job_id]
        if job.clerk_user_id != clerk_user_id:
            raise PortfolioAccessError(
                f"User '{clerk_user_id}' is not authorized to access job '{job_id}'."
            )
        return job

    async def process_sqs_message(self, job_id: str, message_id: str) -> AnalysisJobRecord:
        if job_id not in self._jobs:
            raise KeyError(f"Job '{job_id}' not found.")

        lock = self._job_locks.setdefault(job_id, asyncio.Lock())
        async with lock:
            job = self._jobs[job_id]
            seen_messages = self._processed_sqs_messages.setdefault(job_id, set())
            if message_id in seen_messages and job.status == JobStatus.COMPLETE:
                return job

            job.status = JobStatus.RUNNING
            job.updated_at = datetime.now(timezone.utc)

            for stage in (
                SpecialistName.TAGGER,
                SpecialistName.REPORTER,
                SpecialistName.CHARTER,
                SpecialistName.RETIREMENT,
            ):
                stage_record = job.stages[stage]
                if stage_record.status == StageState.COMPLETE:
                    continue

                stage_record.status = StageState.RUNNING
                stage_record.attempts += 1
                stage_record.error = None
                stage_record.updated_at = datetime.now(timezone.utc)

                try:
                    result = await self._specialists[stage](job)
                    if stage == SpecialistName.CHARTER:
                        raw_allocations = result.get("allocations", [])
                        allocations = [
                            item if isinstance(item, ChartAllocation) else ChartAllocation(**item)
                            for item in raw_allocations
                        ]
                        validated = self.validate_chart_allocations(allocations)
                        job.chart_allocations = validated
                    elif stage == SpecialistName.REPORTER:
                        job.report_markdown = result.get("report_markdown")
                    elif stage == SpecialistName.RETIREMENT:
                        job.retirement_projection = result

                    stage_record.output = result
                    stage_record.status = StageState.COMPLETE
                except Exception as exc:
                    stage_record.status = StageState.FAILED
                    stage_record.error = str(exc)
                finally:
                    stage_record.updated_at = datetime.now(timezone.utc)

            seen_messages.add(message_id)
            job.status = self._compute_job_status(job)
            job.updated_at = datetime.now(timezone.utc)
            return job

    @staticmethod
    def _compute_job_status(job: AnalysisJobRecord) -> JobStatus:
        states = [stage.status for stage in job.stages.values()]
        if all(state == StageState.COMPLETE for state in states):
            return JobStatus.COMPLETE
        if all(state == StageState.FAILED for state in states):
            return JobStatus.FAILED
        if any(state == StageState.FAILED for state in states):
            return JobStatus.PARTIAL
        return JobStatus.RUNNING

    async def _default_tagger(self, job: AnalysisJobRecord) -> Dict[str, Any]:
        tagged = [
            {
                "symbol": pos.symbol,
                "asset_class": pos.asset_class,
                "sector": pos.sector or "Unclassified",
                "unrealized_gain_usd": round(pos.market_value_usd - pos.cost_basis_usd, 2),
            }
            for pos in job.positions
        ]
        return {"tagged_positions": tagged}

    async def _default_reporter(self, job: AnalysisJobRecord) -> Dict[str, Any]:
        total_value = round(sum(p.market_value_usd for p in job.positions), 2)
        total_cost = round(sum(p.cost_basis_usd for p in job.positions), 2)
        markdown = (
            f"# Portfolio Analysis ({job.job_id})\n"
            f"- Positions: {len(job.positions)}\n"
            f"- Total Market Value: ${total_value:,.2f}\n"
            f"- Total Cost Basis: ${total_cost:,.2f}\n"
        )
        return {"report_markdown": markdown, "total_value_usd": total_value}

    async def _default_charter(self, job: AnalysisJobRecord) -> Dict[str, Any]:
        total_value = sum(p.market_value_usd for p in job.positions)
        if total_value <= 0:
            raise ChartAllocationValidationError("Total portfolio market value must be positive.")

        grouped: Dict[str, float] = {}
        for pos in job.positions:
            grouped[pos.asset_class] = grouped.get(pos.asset_class, 0.0) + pos.market_value_usd

        categories = list(grouped.items())
        allocations: List[ChartAllocation] = []
        running_pct = 0.0
        for idx, (category, amount) in enumerate(categories):
            if idx == len(categories) - 1:
                weight = round(100.0 - running_pct, 4)
            else:
                weight = round((amount / total_value) * 100.0, 4)
                running_pct += weight
            allocations.append(
                ChartAllocation(
                    category=category,
                    weight_pct=weight,
                    amount_usd=round(amount, 2),
                )
            )
        return {"allocations": [a.model_dump() for a in allocations]}

    async def _default_retirement(self, job: AnalysisJobRecord) -> Dict[str, Any]:
        total_value = sum(p.market_value_usd for p in job.positions)
        projected_20y = round(total_value * (1.07 ** 20), 2)
        return {
            "assumed_annual_return": 0.07,
            "horizon_years": 20,
            "projected_value_usd": projected_20y,
        }
