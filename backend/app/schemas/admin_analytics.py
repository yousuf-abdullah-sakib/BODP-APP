from datetime import date

from pydantic import BaseModel


class DailyCount(BaseModel):
    date: date
    count: int


class CategoryCount(BaseModel):
    category: str
    count: int


class TopDataset(BaseModel):
    title: str
    count: int


class AnalyticsResponse(BaseModel):
    requests_submitted: int
    approval_rate_pct: float
    total_downloads: int
    new_researchers: int
    requests_over_time: list[DailyCount]
    downloads_over_time: list[DailyCount]
    new_users_over_time: list[DailyCount]
    requests_by_status: dict[str, int]
    requests_by_category: list[CategoryCount]
    top_datasets_by_grants: list[TopDataset]
