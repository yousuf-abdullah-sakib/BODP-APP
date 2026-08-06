import json
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import require_permission
from app.models.user import User
from app.schemas.visualize import BoundaryShapefileSummary
from app.services import boundary_service

router = APIRouter(prefix="/boundary-shapefiles", tags=["boundary"])


@router.get("", response_model=list[BoundaryShapefileSummary])
async def list_boundaries(db: AsyncSession = Depends(get_db)):
    """Public — the admin panel's list view and the public map both read
    this (Master Plan §3 Phase 7 task 5, shared boundary table)."""
    return await boundary_service.list_boundaries(db)


@router.get("/default", response_model=BoundaryShapefileSummary | None)
async def get_default_boundary(db: AsyncSession = Depends(get_db)):
    """Public — the public map fetches this on load, falling back to the
    static /geo/bangladesh-boundary.geojson file client-side when None."""
    return await boundary_service.get_default_boundary(db)


@router.post("", response_model=BoundaryShapefileSummary, status_code=201)
async def create_boundary(
    name: str = Form(...),
    geojson: str = Form(...),
    is_default: bool = Form(default=False),
    current_user: User = Depends(require_permission("Edit Datasets")),
    db: AsyncSession = Depends(get_db),
):
    """Admin upload — the .zip shapefile parse (shpjs) happens client-side,
    same as the prototype; this endpoint accepts the resulting GeoJSON
    directly rather than adding a server-side shapefile-parsing dependency
    for a rarely-used admin path."""
    try:
        parsed = json.loads(geojson)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="Invalid GeoJSON") from exc

    return await boundary_service.create_boundary(
        db, name=name, geojson=parsed, is_default=is_default, uploaded_by=current_user.id
    )


@router.delete("/{boundary_id}", status_code=204)
async def delete_boundary(
    boundary_id: uuid.UUID,
    current_user: User = Depends(require_permission("Edit Datasets")),
    db: AsyncSession = Depends(get_db),
):
    await boundary_service.delete_boundary(db, boundary_id)
