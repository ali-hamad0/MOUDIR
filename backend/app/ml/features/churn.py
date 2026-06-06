"""Churn features + label (Phase 6, Task 6.4). Per-customer RFM as of a cutoff, plus a
forward-looking label, built leakage-free for the classifier (Task 6.8).

The supervised framing:
  - FEATURES are computed from each customer's orders STRICTLY BEFORE `as_of` (recency,
    frequency, monetary, tenure) — what we know at decision time.
  - The LABEL is `churned` = the customer placed NO order in the (as_of, as_of+horizon]
    window. The future window is used ONLY to label, NEVER as a feature (AD-6.6). This
    is the one place "the future" is allowed in, and only to define ground truth.

Definition of "at risk / churned" (documented for DECISIONS / the defend-it question):
  a customer is churned for this cutoff if they have at least one PAST order (they were a
  customer) but make NO order in the next `horizon_days` (default 30). New customers with
  no prior order are excluded from the training set — there is nothing to "re-engage".

Pure functions over the customer_order_history read shape (or raw order tuples), so they
are unit-testable and run on Colab from the exported CSVs.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd

# How far forward we look to decide churn. 30 days ≈ "didn't come back in a month".
DEFAULT_HORIZON_DAYS = 30


@dataclass(frozen=True)
class CustomerOrder:
    """One order for churn purposes: which customer, on which Beirut day, for how much."""

    customer_id: str
    day: date
    amount_lbp: int


def _to_orders(records: Sequence) -> list[CustomerOrder]:
    out: list[CustomerOrder] = []
    for r in records:
        if isinstance(r, CustomerOrder):
            out.append(r)
        else:
            day = r[1].date() if isinstance(r[1], datetime) else r[1]
            out.append(CustomerOrder(customer_id=str(r[0]), day=day, amount_lbp=int(r[2])))
    return out


def build_churn_features(
    orders: Sequence, *, as_of: date, horizon_days: int = DEFAULT_HORIZON_DAYS
) -> pd.DataFrame:
    """Build the churn training table at cutoff `as_of`.

    Input is a flat list of (customer_id, day, amount_lbp) orders (one row per order) —
    NOT the pre-aggregated RFM, because the label needs each order's day to split
    past from the horizon window. One output row per customer with at least one PAST
    order. Columns:
      customer_id, recency_days, frequency, monetary_lbp, avg_order_lbp, tenure_days,
      churned (the label).
    """
    all_orders = _to_orders(orders)
    horizon_end = as_of + timedelta(days=horizon_days)

    past: dict[str, list[CustomerOrder]] = {}
    future_active: set[str] = set()
    for o in all_orders:
        if o.day < as_of:
            past.setdefault(o.customer_id, []).append(o)
        elif as_of <= o.day < horizon_end:
            future_active.add(o.customer_id)

    records = []
    for customer_id, hist in past.items():
        days = [o.day for o in hist]
        amounts = [o.amount_lbp for o in hist]
        frequency = len(hist)
        monetary = sum(amounts)
        last_day = max(days)
        first_day = min(days)
        records.append(
            {
                "customer_id": customer_id,
                "recency_days": (as_of - last_day).days,
                "frequency": frequency,
                "monetary_lbp": monetary,
                "avg_order_lbp": monetary / frequency if frequency else 0,
                "tenure_days": (as_of - first_day).days,
                # Label: churned if NOT seen again in the horizon window.
                "churned": int(customer_id not in future_active),
            }
        )

    if not records:
        return pd.DataFrame(
            columns=[
                "customer_id",
                "recency_days",
                "frequency",
                "monetary_lbp",
                "avg_order_lbp",
                "tenure_days",
                "churned",
            ]
        )
    return pd.DataFrame(records)


def feature_columns() -> list[str]:
    """The classifier's input columns (everything except the id and the label)."""
    return ["recency_days", "frequency", "monetary_lbp", "avg_order_lbp", "tenure_days"]
