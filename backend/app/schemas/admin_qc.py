import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class QualityIssuePublic(BaseModel):
    id: uuid.UUID
    dataset_id: uuid.UUID
    dataset_title: str
    issue_type: str
    severity: str
    status: str
    detail: str | None
    detected_at: datetime

    model_config = {"from_attributes": True}


class QcScanResult(BaseModel):
    issues_found: int
    issues: list[QualityIssuePublic]


class QualityIssueStatusUpdate(BaseModel):
    status: Literal["resolved", "ignored"]
