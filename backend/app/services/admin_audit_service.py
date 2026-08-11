import csv
import io

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLogEntry


def _build_query(
    *,
    search: str | None,
    action_type: str | None,
) -> Select:
    """The SINGLE query-building function used by both the paginated
    JSON-list endpoint and the CSV-export endpoint — structurally
    eliminates the class of bug the prototype has (export always dumping
    the full unfiltered list regardless of the on-screen search/type
    filter state), since there is no second, independently-built query
    for export to drift from this one."""
    stmt = select(AuditLogEntry)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            or_(
                AuditLogEntry.action.ilike(like),
                AuditLogEntry.actor_name.ilike(like),
                AuditLogEntry.actor_email.ilike(like),
                AuditLogEntry.target.ilike(like),
            )
        )
    if action_type:
        stmt = stmt.where(AuditLogEntry.action_type == action_type)
    return stmt.order_by(AuditLogEntry.created_at.desc())


async def list_entries(
    db: AsyncSession, *, search: str | None, action_type: str | None, page: int, page_size: int
) -> tuple[list[AuditLogEntry], int]:
    base = _build_query(search=search, action_type=action_type)

    total = await db.scalar(select(func.count()).select_from(base.subquery()))
    result = await db.execute(base.offset((page - 1) * page_size).limit(page_size))
    return list(result.scalars().all()), total or 0


async def export_csv(db: AsyncSession, *, search: str | None, action_type: str | None) -> str:
    base = _build_query(search=search, action_type=action_type)
    result = await db.execute(base)
    entries = result.scalars().all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Time", "Actor", "Actor Email", "Action", "Target", "Type", "IP Address"])
    for e in entries:
        writer.writerow(
            [
                e.created_at.isoformat(),
                e.actor_name or "",
                e.actor_email or "",
                e.action,
                e.target or "",
                e.action_type,
                e.ip_address or "",
            ]
        )
    return buffer.getvalue()
