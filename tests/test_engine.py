from __future__ import annotations

import asyncio
import pytest

from wealth_planner.engine import (
    AsyncWealthPlannerEngine,
    ChartAllocationValidationError,
    PortfolioAccessError,
)
from wealth_planner.models import (
    ChartAllocation,
    JobStatus,
    PortfolioPosition,
    SpecialistName,
    StageState,
)


def _sample_positions() -> list[PortfolioPosition]:
    return [
        PortfolioPosition(
            symbol="VTI",
            asset_class="Equity",
            shares=100.0,
            market_value_usd=30000.0,
            cost_basis_usd=25000.0,
        ),
        PortfolioPosition(
            symbol="BND",
            asset_class="Fixed Income",
            shares=200.0,
            market_value_usd=15000.0,
            cost_basis_usd=15500.0,
        ),
        PortfolioPosition(
            symbol="VMFXX",
            asset_class="Cash",
            shares=5000.0,
            market_value_usd=5000.0,
            cost_basis_usd=5000.0,
        ),
    ]


def test_cross_user_job_polling_rejected_with_403() -> None:
    engine = AsyncWealthPlannerEngine()
    job = engine.submit_job(
        clerk_user_id="user_alice",
        positions=_sample_positions(),
        idempotency_key="req-alice-1",
    )

    owner_view = engine.get_job(job.job_id, clerk_user_id="user_alice")
    assert owner_view.job_id == job.job_id

    with pytest.raises(PortfolioAccessError) as exc_info:
        engine.get_job(job.job_id, clerk_user_id="user_bob")
    assert exc_info.value.status_code == 403


def test_duplicate_sqs_delivery_skips_completed_stages() -> None:
    call_counts = {stage: 0 for stage in SpecialistName}

    async def counting_tagger(job):
        call_counts[SpecialistName.TAGGER] += 1
        return {"tagged": len(job.positions)}

    async def counting_reporter(job):
        call_counts[SpecialistName.REPORTER] += 1
        return {"report_markdown": "# OK"}

    async def counting_charter(job):
        call_counts[SpecialistName.CHARTER] += 1
        return {
            "allocations": [
                {"category": "Equity", "weight_pct": 60.0, "amount_usd": 30000.0},
                {"category": "Fixed Income", "weight_pct": 40.0, "amount_usd": 20000.0},
            ]
        }

    async def counting_retirement(job):
        call_counts[SpecialistName.RETIREMENT] += 1
        return {"projected_value_usd": 100000.0}

    engine = AsyncWealthPlannerEngine(
        specialists={
            SpecialistName.TAGGER: counting_tagger,
            SpecialistName.REPORTER: counting_reporter,
            SpecialistName.CHARTER: counting_charter,
            SpecialistName.RETIREMENT: counting_retirement,
        }
    )
    job = engine.submit_job("user_alice", _sample_positions(), "idem-1")

    asyncio.run(engine.process_sqs_message(job.job_id, message_id="sqs-1"))
    asyncio.run(engine.process_sqs_message(job.job_id, message_id="sqs-1"))
    asyncio.run(engine.process_sqs_message(job.job_id, message_id="sqs-1-dup"))

    assert job.status == JobStatus.COMPLETE
    assert all(count == 1 for count in call_counts.values())


def test_failed_charter_marks_job_partial_instead_of_complete() -> None:
    async def failing_charter(job):
        raise RuntimeError("Charter rendering service unavailable")

    engine = AsyncWealthPlannerEngine(
        specialists={SpecialistName.CHARTER: failing_charter}
    )
    job = engine.submit_job("user_alice", _sample_positions(), "idem-partial")
    result = asyncio.run(engine.process_sqs_message(job.job_id, message_id="sqs-2"))

    assert result.status == JobStatus.PARTIAL
    assert result.stages[SpecialistName.CHARTER].status == StageState.FAILED
    assert result.stages[SpecialistName.TAGGER].status == StageState.COMPLETE
    assert result.stages[SpecialistName.REPORTER].status == StageState.COMPLETE
    assert result.stages[SpecialistName.RETIREMENT].status == StageState.COMPLETE


def test_chart_allocation_weights_validated_to_100_percent() -> None:
    valid = [
        ChartAllocation(category="Equity", weight_pct=60.0, amount_usd=30000.0),
        ChartAllocation(category="Fixed Income", weight_pct=30.0, amount_usd=15000.0),
        ChartAllocation(category="Cash", weight_pct=10.0, amount_usd=5000.0),
    ]
    validated = AsyncWealthPlannerEngine.validate_chart_allocations(valid)
    assert round(sum(a.weight_pct for a in validated), 4) == 100.0

    invalid = [
        ChartAllocation(category="Equity", weight_pct=60.0, amount_usd=30000.0),
        ChartAllocation(category="Cash", weight_pct=35.0, amount_usd=17500.0),
    ]
    with pytest.raises(ChartAllocationValidationError):
        AsyncWealthPlannerEngine.validate_chart_allocations(invalid)
