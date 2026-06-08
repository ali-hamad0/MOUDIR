"""Data contracts for the FinanceAgent.

Dataclasses carry internal pipeline state (revenue stats, anomaly results); the
Pydantic model is the LLM structured-output contract (validated, retried on failure).
"""

from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel, Field


@dataclass
class RevenueDay:
    day: date
    revenue_lbp: int


@dataclass
class RevenueSummary:
    days_window: int
    total_lbp: int
    daily_avg_lbp: int
    trend: str  # "up" | "down" | "stable"
    days: list[RevenueDay]


@dataclass
class AnomalyResult:
    flagged_days: list[RevenueDay]
    total_days: int
    anomaly_count: int


class FinanceReply(BaseModel):
    """LLM-structured reply for the compose_reply step.

    A single non-empty Lebanese Arabic string explaining the revenue and anomalies.
    The model summarises what the ML detector found; it does not produce the flags
    themselves (Constitution IV).
    """

    reply_ar: str = Field(min_length=1)
