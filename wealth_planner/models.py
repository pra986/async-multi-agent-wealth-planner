from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, field_validator


class SpecialistName(str, Enum):
    TAGGER = "tagger"
    REPORTER = "reporter"
    CHARTER = "charter"
    RETIREMENT = "retirement"


class StageState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class PortfolioPosition(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=16)
    asset_class: str = Field(..., min_length=1)
    shares: float = Field(..., gt=0)
    market_value_usd: float = Field(..., ge=0.0)
    cost_basis_usd: float = Field(..., ge=0.0)
    sector: Optional[str] = None
    account_type: str = Field(default="taxable")

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()


class ChartAllocation(BaseModel):
    category: str = Field(..., min_length=1)
    weight_pct: float = Field(..., ge=0.0, le=100.0)
    amount_usd: float = Field(..., ge=0.0)


class SpecialistStageStatus(BaseModel):
    stage: SpecialistName
    status: StageState = StageState.PENDING
    attempts: int = Field(default=0, ge=0)
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AnalysisJobRecord(BaseModel):
    job_id: str
    clerk_user_id: str = Field(..., min_length=1)
    idempotency_key: str = Field(..., min_length=1)
    status: JobStatus = JobStatus.QUEUED
    positions: List[PortfolioPosition] = Field(default_factory=list)
    stages: Dict[SpecialistName, SpecialistStageStatus] = Field(
        default_factory=lambda: {
            stage: SpecialistStageStatus(stage=stage) for stage in SpecialistName
        }
    )
    chart_allocations: List[ChartAllocation] = Field(default_factory=list)
    report_markdown: Optional[str] = None
    retirement_projection: Optional[Dict[str, Any]] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
