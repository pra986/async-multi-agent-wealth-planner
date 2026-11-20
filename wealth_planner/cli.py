from __future__ import annotations

import asyncio
import json

from wealth_planner.engine import AsyncWealthPlannerEngine
from wealth_planner.models import PortfolioPosition


async def run_demo() -> None:
    engine = AsyncWealthPlannerEngine()
    positions = [
        PortfolioPosition(
            symbol="VTI",
            asset_class="Equity",
            shares=120.0,
            market_value_usd=33600.0,
            cost_basis_usd=28000.0,
            sector="Broad Market",
            account_type="taxable",
        ),
        PortfolioPosition(
            symbol="BND",
            asset_class="Fixed Income",
            shares=150.0,
            market_value_usd=10800.0,
            cost_basis_usd=11200.0,
            sector="Aggregate Bond",
            account_type="ira",
        ),
        PortfolioPosition(
            symbol="VMFXX",
            asset_class="Cash",
            shares=5600.0,
            market_value_usd=5600.0,
            cost_basis_usd=5600.0,
            sector="Money Market",
            account_type="taxable",
        ),
    ]

    job = engine.submit_job(
        clerk_user_id="user_clerk_demo_01",
        positions=positions,
        idempotency_key="demo-run-001",
    )
    completed = await engine.process_sqs_message(job.job_id, message_id="sqs-msg-001")

    summary = {
        "job_id": completed.job_id,
        "status": completed.status.value,
        "stages": {k.value: v.status.value for k, v in completed.stages.items()},
        "chart_allocations": [a.model_dump() for a in completed.chart_allocations],
        "allocation_sum_pct": round(sum(a.weight_pct for a in completed.chart_allocations), 4),
        "retirement_projection": completed.retirement_projection,
    }
    print(json.dumps(summary, indent=2))


def main() -> None:
    asyncio.run(run_demo())


if __name__ == "__main__":
    main()
