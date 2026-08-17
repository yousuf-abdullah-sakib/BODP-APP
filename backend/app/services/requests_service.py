import uuid
from datetime import UTC, date, datetime

from dateutil.relativedelta import relativedelta
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.audit import AuditActionType
from app.models.catalog import Dataset, DatasetStatus
from app.models.requests import (
    AccessGrant,
    DatasetRequest,
    ExtractionStatus,
    GrantStatus,
    RequestStatus,
    SubsetExtraction,
)
from app.models.user import User
from app.schemas.requests import GrantDuration, SearchCriteriaSchema
from app.services import catalog_service
from app.services.audit_service import write_audit_log
from app.services.catalog_service import RecordsFilter


def validate_scope_within_grant(requested: SearchCriteriaSchema, grant_scope: dict | None) -> None:
    """Rejects an extraction request whose scope reaches outside what the
    grant actually authorizes (Master Plan §3 Phase 5 explicit quality
    check: "requesting a scope outside the grant's approved bounds is
    rejected server-side"). A grant with no scope set (None) is ungoverned
    — any requested scope is allowed. Raises HTTPException(422) with a
    field-specific reason on the first violation found."""
    if grant_scope is None:
        return

    for field in ("category", "source", "quality", "platform", "station", "format", "processing_level"):
        grant_value = grant_scope.get(field)
        requested_value = getattr(requested, field)
        if grant_value and requested_value and requested_value != grant_value:
            raise HTTPException(
                status_code=422,
                detail=f"Requested {field} '{requested_value}' is outside the grant's approved {field} '{grant_value}'.",
            )

    # parameters is a list (checkbox multi-select) — the requested set must
    # be a SUBSET of the grant's approved parameters, not an exact match.
    # An empty/absent grant_parameters means the grant itself was approved
    # with no parameter restriction (all parameters allowed), matching how
    # RecordsFilter/scope_filter.py both treat an empty list as "no
    # filtering" rather than "filter to nothing."
    grant_parameters = grant_scope.get("parameters")
    if grant_parameters and requested.parameters:
        disallowed = set(requested.parameters) - set(grant_parameters)
        if disallowed:
            raise HTTPException(
                status_code=422,
                detail=f"Requested parameter(s) {sorted(disallowed)} are outside the grant's "
                f"approved parameters {sorted(grant_parameters)}.",
            )

    grant_date_from = grant_scope.get("date_from")
    if grant_date_from and requested.date_from and requested.date_from < grant_date_from:
        raise HTTPException(
            status_code=422,
            detail=f"Requested date_from {requested.date_from} is earlier than the grant's approved date_from {grant_date_from}.",
        )
    grant_date_to = grant_scope.get("date_to")
    if grant_date_to and requested.date_to and requested.date_to > grant_date_to:
        raise HTTPException(
            status_code=422,
            detail=f"Requested date_to {requested.date_to} is later than the grant's approved date_to {grant_date_to}.",
        )

    grant_depth_min = grant_scope.get("depth_min")
    if grant_depth_min is not None and requested.depth_min is not None and requested.depth_min < grant_depth_min:
        raise HTTPException(
            status_code=422,
            detail=f"Requested depth_min {requested.depth_min} is shallower than the grant's approved depth_min {grant_depth_min}.",
        )
    grant_depth_max = grant_scope.get("depth_max")
    if grant_depth_max is not None and requested.depth_max is not None and requested.depth_max > grant_depth_max:
        raise HTTPException(
            status_code=422,
            detail=f"Requested depth_max {requested.depth_max} is deeper than the grant's approved depth_max {grant_depth_max}.",
        )

    grant_bounds = grant_scope.get("bounds")
    if grant_bounds and requested.bounds:
        r = requested.bounds
        if (
            r.lat_min < grant_bounds["lat_min"]
            or r.lat_max > grant_bounds["lat_max"]
            or r.lon_min < grant_bounds["lon_min"]
            or r.lon_max > grant_bounds["lon_max"]
        ):
            raise HTTPException(
                status_code=422,
                detail="Requested spatial bounds extend outside the grant's approved bounds.",
            )


def compute_expiry(
    from_dt: datetime, duration: GrantDuration, custom_expires_at: date | None
) -> datetime:
    """Mirrors the prototype's computeExpiry() exactly (see
    bodp-frontend/src/context/AdminDataContext.tsx lines 153-177) — same
    duration keys, same "from" semantics. Callers control what "from" means:
    approval computes from today, extension computes from the grant's
    *current* expires_at (Master Plan §3 Phase 4 task 6 — preserve this)."""
    if duration == GrantDuration.CUSTOM:
        assert custom_expires_at is not None  # enforced by schema validator
        return datetime.combine(custom_expires_at, datetime.min.time(), tzinfo=UTC)
    if duration == GrantDuration.FIVE_DAYS:
        return from_dt + relativedelta(days=5)
    if duration == GrantDuration.TEN_DAYS:
        return from_dt + relativedelta(days=10)
    if duration == GrantDuration.ONE_MONTH:
        return from_dt + relativedelta(months=1)
    if duration == GrantDuration.TWO_MONTHS:
        return from_dt + relativedelta(months=2)
    if duration == GrantDuration.SIX_MONTHS:
        return from_dt + relativedelta(months=6)
    if duration == GrantDuration.ONE_YEAR:
        return from_dt + relativedelta(years=1)
    raise ValueError(f"Unknown duration: {duration!r}")


async def create_request(
    db: AsyncSession,
    *,
    user: User,
    dataset_id: uuid.UUID,
    justification: str,
    search_criteria: SearchCriteriaSchema | None,
) -> DatasetRequest:
    result = await db.execute(
        select(Dataset).where(
            Dataset.id == dataset_id, Dataset.status == DatasetStatus.PUBLISHED.value
        )
    )
    dataset = result.scalar_one_or_none()
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")

    # Coverage snapshot — computed ONCE, here, at request creation, and
    # never again. This replaces the admin dashboard's previous behavior
    # of recomputing the same numbers from scratch on every page load
    # (profiled at ~96% of that endpoint's server time) — same
    # RecordsFilter/catalog_service entry point get_request_coverage
    # already used for a single-request lookup, just called once at
    # creation instead of on every list view.
    f = _records_filter_from_search_criteria(
        search_criteria.model_dump(exclude_none=True) if search_criteria else None
    )
    matching, total = await catalog_service.get_matching_record_counts(db, dataset_id, f)
    percent = round((matching / total) * 100, 1) if total > 0 else 0.0

    request = DatasetRequest(
        user_id=user.id,
        dataset_id=dataset_id,
        justification=justification,
        search_criteria=search_criteria.model_dump(exclude_none=True) if search_criteria else None,
        status=RequestStatus.PENDING.value,
        matching_record_count=matching,
        dataset_total_record_count=total,
        matching_percent=percent,
        dataset_version=dataset.version,
    )
    db.add(request)
    await db.flush()

    # Notification for this event is dispatched by the caller via
    # send_request_submitted.delay() (see routers/requests.py), which
    # targets admins holding "Approve Requests" specifically — the ones who
    # can actually act on it — rather than every coarse role='admin' user
    # here. Two separate calls for the same event previously double-notified
    # any admin who satisfied both selections.

    await db.commit()
    await db.refresh(request)
    return await _get_request_with_relations(db, request.id)


async def _get_request_with_relations(db: AsyncSession, request_id: uuid.UUID) -> DatasetRequest:
    result = await db.execute(
        select(DatasetRequest)
        .options(
            selectinload(DatasetRequest.dataset).selectinload(Dataset.category),
            selectinload(DatasetRequest.user),
        )
        .where(DatasetRequest.id == request_id)
    )
    request = result.scalar_one_or_none()
    if request is None:
        raise HTTPException(status_code=404, detail="Request not found")
    return request


def _records_filter_from_search_criteria(search_criteria: dict | None) -> RecordsFilter:
    """Builds a RecordsFilter from a stored search_criteria dict (same shape
    as SearchCriteriaSchema) for computing how much of a dataset a
    request's filters actually match. An empty/None search_criteria
    produces an all-inclusive filter (matches the "no filters = all
    records" semantics used everywhere else this scope is interpreted).

    date_from/date_to are stored as ISO strings in the JSONB column (same
    as everywhere else search_criteria is persisted) — RecordsFilter/
    DatasetRecord.time expects real date objects, same conversion FastAPI's
    Query(date | None) does automatically for the /records endpoint."""
    criteria = search_criteria or {}
    bounds = criteria.get("bounds")
    raw_date_from = criteria.get("date_from")
    raw_date_to = criteria.get("date_to")
    return RecordsFilter(
        parameters=criteria.get("parameters"),
        quality=criteria.get("quality"),
        date_from=date.fromisoformat(raw_date_from) if raw_date_from else None,
        date_to=date.fromisoformat(raw_date_to) if raw_date_to else None,
        lat_min=bounds.get("lat_min") if bounds else None,
        lat_max=bounds.get("lat_max") if bounds else None,
        lon_min=bounds.get("lon_min") if bounds else None,
        lon_max=bounds.get("lon_max") if bounds else None,
        depth_min=criteria.get("depth_min"),
        depth_max=criteria.get("depth_max"),
        source=criteria.get("source"),
        platform=criteria.get("platform"),
        station=criteria.get("station"),
        format_=criteria.get("format"),
        processing_level=criteria.get("processing_level"),
    )


def read_request_coverage_snapshot(request: DatasetRequest) -> dict:
    """Reads the coverage snapshot already stored on this request (see
    create_request) rather than recomputing it — the single-request
    equivalent of list_requests_for_admin's per-row logic, used by the
    reject endpoint's response (routers/admin_requests.py) so rejecting
    a request doesn't pay a live query either. Requires request.dataset
    to already be loaded (selectinload'd) — does not lazy-load."""
    is_stale = (
        request.dataset_version is not None
        and request.dataset is not None
        and request.dataset_version != request.dataset.version
    )
    return {
        "matching_record_count": request.matching_record_count,
        "dataset_total_record_count": request.dataset_total_record_count,
        "matching_percent": request.matching_percent,
        "is_stale": is_stale,
    }


async def get_request_coverage(db: AsyncSession, request: DatasetRequest) -> dict:
    """Computes matching_record_count/dataset_total_record_count/
    matching_percent for one request's own search_criteria, so the admin
    queue can show "how much of this dataset does the request cover"
    without an extra round trip per row.

    Disables Postgres JIT for this session only (SET LOCAL, scoped to the
    current transaction — never a global/persistent setting change).
    Measured via EXPLAIN ANALYZE against a real 723K-row dataset with a
    spatial bbox filter: JIT compilation added ~425ms of pure overhead
    (980ms -> 555ms with jit=off) to what's fundamentally a cheap
    ad-hoc aggregate query, not the kind of hot/repeated query JIT is
    meant to help — it was mis-triggering on query complexity (GiST
    index + bitmap AND across 3 indexes), not actual cost."""
    await db.execute(text("SET LOCAL jit = off"))
    f = _records_filter_from_search_criteria(request.search_criteria)
    matching, total = await catalog_service.get_matching_record_counts(db, request.dataset_id, f)
    percent = round((matching / total) * 100, 1) if total > 0 else 0.0
    return {
        "matching_record_count": matching,
        "dataset_total_record_count": total,
        "matching_percent": percent,
    }


async def get_request_for_admin(db: AsyncSession, request_id: uuid.UUID) -> DatasetRequest:
    return await _get_request_with_relations(db, request_id)


async def list_requests_for_user(
    db: AsyncSession, user_id: uuid.UUID, *, status_filter: str | None = None
) -> list[DatasetRequest]:
    query = (
        select(DatasetRequest)
        .options(selectinload(DatasetRequest.dataset).selectinload(Dataset.category))
        .where(DatasetRequest.user_id == user_id)
        .order_by(DatasetRequest.submitted_at.desc())
    )
    if status_filter:
        query = query.where(DatasetRequest.status == status_filter)
    result = await db.execute(query)
    return list(result.scalars().all())


async def list_requests_for_admin(
    db: AsyncSession, *, status_filter: str | None = None
) -> list[tuple[DatasetRequest, dict]]:
    """Returns each request paired with its dataset-coverage figures
    (matching_record_count/dataset_total_record_count/matching_percent,
    is_stale) — lets the admin queue show "how much of the dataset" at a
    glance for every row.

    Reads the SNAPSHOT captured once at request-creation time (see
    create_request) instead of recomputing it here. This used to
    recompute from scratch, concurrently, for every request on every
    page load — profiled at ~96% of this endpoint's server time (one
    request referencing a 723K-row dataset with a spatial filter cost
    ~1.6s alone under concurrent load; two referencing a 12M-element
    Zarr-backed dataset cost ~3.2s each). None of that recomputation
    happens here anymore.

    is_stale is derived, not stored: True when the request's captured
    dataset_version no longer matches the dataset's current version,
    meaning ingestion has changed this dataset's contents since the
    snapshot was taken — surfaced so an admin can tell "this number may
    no longer be accurate" without the endpoint silently recomputing it
    (which would reintroduce the exact cost this change removes).

    A request created before this snapshot mechanism existed has
    matching_record_count=None (nullable column, never backfilled — see
    the migration's docstring) — returned as-is, not fabricated as 0 or
    silently recomputed live; the router/frontend render this as "not
    available" for those legacy rows specifically."""
    query = (
        select(DatasetRequest)
        .options(
            selectinload(DatasetRequest.dataset).selectinload(Dataset.category),
            selectinload(DatasetRequest.user),
        )
        .order_by(DatasetRequest.submitted_at.desc())
    )
    if status_filter:
        query = query.where(DatasetRequest.status == status_filter)
    result = await db.execute(query)
    requests = list(result.scalars().all())

    pairs = []
    for r in requests:
        is_stale = (
            r.dataset_version is not None
            and r.dataset is not None
            and r.dataset_version != r.dataset.version
        )
        pairs.append(
            (
                r,
                {
                    "matching_record_count": r.matching_record_count,
                    "dataset_total_record_count": r.dataset_total_record_count,
                    "matching_percent": r.matching_percent,
                    "is_stale": is_stale,
                },
            )
        )
    return pairs


async def approve_request(
    db: AsyncSession,
    *,
    request: DatasetRequest,
    admin: User,
    search_criteria_override: SearchCriteriaSchema | None,
    note: str | None,
    duration: GrantDuration,
    custom_expires_at: date | None,
    ip_address: str | None,
) -> AccessGrant:
    if request.status != RequestStatus.PENDING.value:
        raise HTTPException(
            status_code=409, detail=f"Request is already {request.status}, cannot approve again"
        )

    now = datetime.now(UTC)
    expires_at = compute_expiry(now, duration, custom_expires_at)

    # An override passed directly to this approve call takes precedence;
    # otherwise the grant's scope is exactly the user's original,
    # untouched search_criteria. request.search_criteria itself is NEVER
    # written to below — it stays exactly what the user originally
    # submitted, permanently.
    if search_criteria_override is not None:
        scope = search_criteria_override.model_dump(exclude_none=True)
    else:
        scope = request.search_criteria

    grant = AccessGrant(
        user_id=request.user_id,
        dataset_id=request.dataset_id,
        request_id=request.id,
        granted_by=admin.id,
        granted_at=now,
        expires_at=expires_at,
        status=GrantStatus.ACTIVE.value,
        scope=scope,
    )
    db.add(grant)

    request.status = RequestStatus.APPROVED.value
    request.reviewed_by = admin.id
    request.reviewed_at = now
    request.updated_at = now
    if note:
        request.admin_note = note

    requester = await db.get(User, request.user_id)
    if requester is not None:
        requester.datasets_granted = (requester.datasets_granted or 0) + 1

    await write_audit_log(
        db,
        actor=admin,
        action="Approved request",
        action_type=AuditActionType.APPROVE,
        target=f"{request.id} ({requester.full_name if requester else request.user_id})",
        ip_address=ip_address,
    )

    await db.commit()
    await db.refresh(grant)
    return await _get_grant_with_relations(db, grant.id)


async def reject_request(
    db: AsyncSession,
    *,
    request: DatasetRequest,
    admin: User,
    reason: str,
    ip_address: str | None,
) -> DatasetRequest:
    if request.status != RequestStatus.PENDING.value:
        raise HTTPException(
            status_code=409, detail=f"Request is already {request.status}, cannot reject again"
        )

    now = datetime.now(UTC)
    request.status = RequestStatus.REJECTED.value
    request.reviewed_by = admin.id
    request.reviewed_at = now
    request.updated_at = now
    request.admin_note = reason

    requester = await db.get(User, request.user_id)

    await write_audit_log(
        db,
        actor=admin,
        action="Rejected request",
        action_type=AuditActionType.REJECT,
        target=f"{request.id} ({requester.full_name if requester else request.user_id})",
        ip_address=ip_address,
    )

    await db.commit()
    await db.refresh(request)
    return await _get_request_with_relations(db, request.id)


async def _get_grant_with_relations(db: AsyncSession, grant_id: uuid.UUID) -> AccessGrant:
    result = await db.execute(
        select(AccessGrant)
        .options(
            selectinload(AccessGrant.dataset).selectinload(Dataset.category),
            selectinload(AccessGrant.user),
        )
        .where(AccessGrant.id == grant_id)
    )
    grant = result.scalar_one_or_none()
    if grant is None:
        raise HTTPException(status_code=404, detail="Grant not found")
    return grant


async def get_grant_for_admin(db: AsyncSession, grant_id: uuid.UUID) -> AccessGrant:
    return await _get_grant_with_relations(db, grant_id)


async def list_grants_for_user(db: AsyncSession, user_id: uuid.UUID) -> list[AccessGrant]:
    result = await db.execute(
        select(AccessGrant)
        .options(selectinload(AccessGrant.dataset).selectinload(Dataset.category))
        .where(AccessGrant.user_id == user_id)
        .order_by(AccessGrant.granted_at.desc())
    )
    return list(result.scalars().all())


async def list_grants_for_dataset(
    db: AsyncSession, dataset_id: uuid.UUID, *, status_filter: str | None = None
) -> list[AccessGrant]:
    query = (
        select(AccessGrant)
        .options(selectinload(AccessGrant.user))
        .where(AccessGrant.dataset_id == dataset_id)
        .order_by(AccessGrant.granted_at.desc())
    )
    if status_filter:
        query = query.where(AccessGrant.status == status_filter)
    result = await db.execute(query)
    return list(result.scalars().all())


async def list_grants_for_admin(
    db: AsyncSession, *, status_filter: str | None = None
) -> list[AccessGrant]:
    query = (
        select(AccessGrant)
        .options(
            selectinload(AccessGrant.dataset).selectinload(Dataset.category),
            selectinload(AccessGrant.user),
        )
        .order_by(AccessGrant.granted_at.desc())
    )
    if status_filter:
        query = query.where(AccessGrant.status == status_filter)
    result = await db.execute(query)
    return list(result.scalars().all())


async def extend_grant(
    db: AsyncSession,
    *,
    grant: AccessGrant,
    admin: User,
    duration: GrantDuration,
    custom_expires_at: date | None,
    ip_address: str | None,
) -> AccessGrant:
    if grant.status != GrantStatus.ACTIVE.value:
        raise HTTPException(status_code=409, detail=f"Grant is {grant.status}, cannot extend")

    # Recompute from the grant's CURRENT expiry, not from today — this is the
    # one explicit "preserve this exact prototype behavior" instruction in
    # Master Plan §3 Phase 4 task 6.
    grant.expires_at = compute_expiry(grant.expires_at, duration, custom_expires_at)

    grantee = await db.get(User, grant.user_id)
    dataset = await db.get(Dataset, grant.dataset_id)

    await write_audit_log(
        db,
        actor=admin,
        action="Extended access grant",
        action_type=AuditActionType.DATASET,
        target=f"{grant.id} — {grantee.full_name if grantee else grant.user_id} / "
        f"{dataset.title if dataset else grant.dataset_id}",
        ip_address=ip_address,
    )

    await db.commit()
    await db.refresh(grant)
    return await _get_grant_with_relations(db, grant.id)


async def revoke_grant(
    db: AsyncSession,
    *,
    grant: AccessGrant,
    admin: User,
    ip_address: str | None,
) -> AccessGrant:
    if grant.status != GrantStatus.ACTIVE.value:
        raise HTTPException(status_code=409, detail=f"Grant is already {grant.status}")

    grant.status = GrantStatus.REVOKED.value

    grantee = await db.get(User, grant.user_id)
    if grantee is not None:
        grantee.datasets_granted = max(0, (grantee.datasets_granted or 0) - 1)

    dataset = await db.get(Dataset, grant.dataset_id)

    # Invalidate any in-flight extraction for this grant — a revoked grant
    # must not let a queued/processing extraction complete and become
    # downloadable after access was pulled (Master Plan §3 Phase 5).
    in_flight = await db.execute(
        select(SubsetExtraction).where(
            SubsetExtraction.grant_id == grant.id,
            SubsetExtraction.status.in_(
                [ExtractionStatus.QUEUED.value, ExtractionStatus.PROCESSING.value]
            ),
        )
    )
    for extraction in in_flight.scalars().all():
        extraction.status = ExtractionStatus.FAILED.value
        extraction.error_message = "Grant revoked before extraction completed."

    await write_audit_log(
        db,
        actor=admin,
        action="Revoked access grant",
        action_type=AuditActionType.REVOKE,
        target=f"{grant.id} — {grantee.full_name if grantee else grant.user_id} / "
        f"{dataset.title if dataset else grant.dataset_id}",
        ip_address=ip_address,
    )

    await db.commit()
    await db.refresh(grant)
    return await _get_grant_with_relations(db, grant.id)


async def count_pending_requests(db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count()).select_from(DatasetRequest).where(
            DatasetRequest.status == RequestStatus.PENDING.value
        )
    )
    return result.scalar_one()
