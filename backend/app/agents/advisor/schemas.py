"""Data contracts for the AdvisorAgent morning briefing (Task 7.5).

Dataclasses carry internal pipeline state; the Pydantic model is the LLM
structured-output contract for the final briefing (validated, retried on failure).

Constitution IV: every numeric field here originates from a Phase 6 ML model or
a live DB query — the LLM never produces the numbers, it only synthesizes them.
"""

from dataclasses import dataclass, field
from datetime import date

from pydantic import BaseModel, Field


@dataclass
class ProductRevenue:
    name_ar: str
    revenue_lbp: int


@dataclass
class TodaySnapshot:
    today_revenue_lbp: int
    avg_7day_lbp: int  # average daily revenue over the preceding 7 days
    same_day_last_week_lbp: int
    order_count: int
    top_products: list[ProductRevenue]  # top 3 by revenue today


@dataclass
class DemandForecastItem:
    product_name: str
    current_stock: int
    predicted_units_7d: int  # demand model output (None → 0, no signal)


@dataclass
class DemandForecast:
    items: list[DemandForecastItem] = field(default_factory=list)


@dataclass
class ChurnSummary:
    count_at_risk: int  # customers with churn probability > 0.5
    total_customers: int  # customers with any order history
    top_names: list[str]  # display names of top 3 at-risk customers


@dataclass
class AnomalyStatus:
    is_today_anomalous: bool
    recent_flag_count: int  # how many days were anomalous in the detector window
    recent_flags: list[date]  # the flagged dates (sorted ascending)


class Briefing(BaseModel):
    """LLM-structured morning briefing in Lebanese Arabic.

    The model synthesizes TodaySnapshot + DemandForecast + ChurnSummary +
    AnomalyStatus into a concise, actionable summary. Constitution IV: the LLM
    receives pre-computed structured data — it explains, it does not compute.
    """

    briefing_ar: str = Field(min_length=1)
