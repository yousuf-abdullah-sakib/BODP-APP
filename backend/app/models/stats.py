from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import UUIDPKMixin


class DailyStatsSnapshot(UUIDPKMixin, Base):
    """One row per calendar day, captured by a daily Celery beat job —
    backs the admin Overview dashboard's real trend arrows/%-deltas/
    sparklines (Master Plan §3 Phase 8 task 7). Never fabricated: with
    fewer than 2 rows, the Overview aggregation reports no trend data
    rather than a fake percentage."""

    __tablename__ = "daily_stats_snapshots"

    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False, unique=True, index=True)
    total_users: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_datasets: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pending_requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_downloads: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_grants: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    storage_used_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
