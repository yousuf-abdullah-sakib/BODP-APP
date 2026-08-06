import uuid

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin import BoundaryShapefile


async def list_boundaries(db: AsyncSession) -> list[BoundaryShapefile]:
    result = await db.execute(select(BoundaryShapefile).order_by(BoundaryShapefile.uploaded_at.desc()))
    return list(result.scalars().all())


async def get_default_boundary(db: AsyncSession) -> BoundaryShapefile | None:
    result = await db.execute(select(BoundaryShapefile).where(BoundaryShapefile.is_default.is_(True)))
    return result.scalar_one_or_none()


async def create_boundary(
    db: AsyncSession,
    *,
    name: str,
    geojson: dict,
    is_default: bool,
    uploaded_by: uuid.UUID | None,
) -> BoundaryShapefile:
    if is_default:
        # Only one row may be the default at a time — the model itself has
        # no uniqueness constraint for this, enforced here instead.
        await db.execute(update(BoundaryShapefile).values(is_default=False))

    boundary = BoundaryShapefile(name=name, geojson=geojson, is_default=is_default, uploaded_by=uploaded_by)
    db.add(boundary)
    await db.commit()
    await db.refresh(boundary)
    return boundary


async def delete_boundary(db: AsyncSession, boundary_id: uuid.UUID) -> None:
    boundary = await db.get(BoundaryShapefile, boundary_id)
    if boundary is None:
        raise HTTPException(status_code=404, detail="Boundary shapefile not found")
    await db.delete(boundary)
    await db.commit()
