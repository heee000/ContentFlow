"""Shared API/collector boundaries; legacy invalid observations are not zeros."""

from typing import Annotated

from pydantic import BaseModel, Field


METRIC_FIELDS = ("impressions", "clicks", "likes", "comments", "shares")
MAX_METRIC_VALUE = 9_007_199_254_740_991
COUNTER_RANGE_SQL = " AND ".join(
    f"{field} >= 0 AND {field} <= {MAX_METRIC_VALUE}" for field in METRIC_FIELDS
)
MetricValue = Annotated[
    float, Field(strict=True, ge=0, le=MAX_METRIC_VALUE, allow_inf_nan=False)
]


class MetricValues(BaseModel):
    impressions: MetricValue = 0
    clicks: MetricValue = 0
    likes: MetricValue = 0
    comments: MetricValue = 0
    shares: MetricValue = 0
