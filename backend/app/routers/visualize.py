import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.limiter import limiter
from app.schemas.catalog import StationOption
from app.schemas.visualize import (
    ComparisonJobResponse,
    ComparisonJobStatus,
    ComparisonRequest,
    CoverageRequest,
    CoverageResponse,
    ProfilesRequest,
    ProfilesResponse,
    SpatialJobStatusResponse,
    SpatialRequest,
    SpatialResponse,
    StatisticsJobResponse,
    StatisticsJobStatus,
    StatisticsRequest,
    TimeSeriesRequest,
    TimeSeriesResponse,
    VisualizableDatasetSummary,
    VizFilterParams,
)
from app.services import visualize_service

router = APIRouter(prefix="/visualize", tags=["visualize"])


@router.get("/datasets", response_model=list[VisualizableDatasetSummary])
async def visualizable_datasets(db: AsyncSession = Depends(get_db)):
    """Backs the Visualize module's dataset selector (PLAN.md Phase 4) —
    only datasets an admin has reviewed with at least one approved
    Visualization Variable appear here."""
    return await visualize_service.get_visualizable_datasets(db)


@router.post("/timeseries", response_model=TimeSeriesResponse)
@limiter.limit(settings.RATE_LIMIT_MUTATIONS)
async def timeseries(request: Request, body: TimeSeriesRequest, db: AsyncSession = Depends(get_db)):
    """Real aggregation from dataset_records (Master Plan §3 Phase 7 task 1)
    — trend/moving-average/seasonal/climatology/rate-of-change/anomaly,
    replacing the prototype's synthetic genTimeSeries. Public, no auth
    (matches /catalog/search's trust level — already-published aggregate
    data)."""
    return await visualize_service.get_timeseries(db, body)


@router.post("/spatial", response_model=SpatialResponse)
@limiter.limit(settings.RATE_LIMIT_MUTATIONS)
async def spatial(request: Request, body: SpatialRequest, db: AsyncSession = Depends(get_db)):
    """Light requests compute synchronously; heavy ones dispatch to Celery
    (Master Plan §3 Phase 7 task 2) — same schema either way, caller checks
    `.status`."""
    return await visualize_service.create_spatial_request(db, body)


@router.get("/spatial/{job_id}", response_model=SpatialJobStatusResponse)
async def spatial_job_status(job_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    job = await visualize_service.get_visualization_job(db, job_id)
    result = job.result or {}
    return SpatialJobStatusResponse(
        id=job.id,
        status=job.status,
        points=result.get("points"),
        grid=result.get("grid"),
        method_used=result.get("method_used"),
        error_message=job.error_message,
        created_at=job.created_at,
        completed_at=job.completed_at,
    )


@router.post("/comparison", response_model=ComparisonJobResponse)
@limiter.limit(settings.RATE_LIMIT_MUTATIONS)
async def comparison(request: Request, body: ComparisonRequest, db: AsyncSession = Depends(get_db)):
    """Real paired-series extraction for scatter/regression/correlation-
    matrix (Master Plan §3 Phase 7 task 3) — Pearson r computed server-side
    against real data. Light requests compute synchronously; requests whose
    Parquet/Zarr files are large enough dispatch to Celery (Visualize
    Performance plan, Phase 4) — same envelope either way, caller checks
    `.status`."""
    return await visualize_service.get_comparison(db, body)


@router.get("/comparison/{job_id}", response_model=ComparisonJobStatus)
async def comparison_job_status(job_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    job = await visualize_service.get_visualization_job(db, job_id)
    return ComparisonJobStatus(
        id=job.id,
        status=job.status,
        result=job.result,
        error_message=job.error_message,
        created_at=job.created_at,
        completed_at=job.completed_at,
    )


@router.post("/statistics", response_model=StatisticsJobResponse)
@limiter.limit(settings.RATE_LIMIT_MUTATIONS)
async def statistics(request: Request, body: StatisticsRequest, db: AsyncSession = Depends(get_db)):
    """Box plots, histograms, annual anomalies, decomposition, calendar
    heatmap (Master Plan §3 Phase 7 task 4), all from real aggregated
    queries. Light requests compute synchronously; heavy ones dispatch to
    Celery (Visualize Performance plan, Phase 4) — same envelope either
    way, caller checks `.status`."""
    return await visualize_service.get_statistics(db, body)


@router.get("/statistics/{job_id}", response_model=StatisticsJobStatus)
async def statistics_job_status(job_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    job = await visualize_service.get_visualization_job(db, job_id)
    return StatisticsJobStatus(
        id=job.id,
        status=job.status,
        result=job.result,
        error_message=job.error_message,
        created_at=job.created_at,
        completed_at=job.completed_at,
    )


@router.post("/profiles", response_model=ProfilesResponse)
@limiter.limit(settings.RATE_LIMIT_MUTATIONS)
async def profiles(request: Request, body: ProfilesRequest, db: AsyncSession = Depends(get_db)):
    """Oceanographic Profiles module — vertical depth profiles (Temperature/
    Salinity/Density/Sound Speed/Pressure vs Depth) or a Temperature-
    Salinity diagram, depending on which of `parameter` vs
    `temperature_parameter`+`salinity_parameter` is supplied."""
    return await visualize_service.get_profiles(db, body)


@router.post("/stations", response_model=list[StationOption])
async def filtered_stations(body: VizFilterParams, db: AsyncSession = Depends(get_db)):
    """Supports useVizFilters.ts's filteredStations — stations matching the
    current bbox/depth filters, kept server-side rather than duplicating
    Station-fetching logic client-side."""
    stations = await visualize_service.get_filtered_stations(db, body)
    return [
        StationOption(code=s.code, name=s.name, lat=float(s.lat), lon=float(s.lon)) for s in stations
    ]


@router.post("/coverage", response_model=CoverageResponse)
async def coverage(body: CoverageRequest, db: AsyncSession = Depends(get_db)):
    """Matching/dataset-total counts + coverage percent for the current
    filter state (Visualization & Filter Reliability investigation, issue
    #5) — mirrors the catalog detail page's own matching_count/dataset_
    total_count summary, reusing the same underlying count mechanism
    (catalog_service.get_matching_record_counts), not a new full scan."""
    return await visualize_service.get_visualize_coverage(db, body)
