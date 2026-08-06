"""Seeds real Postgres data for local development/testing of the Phase 3
catalog endpoints — stations, categories, and datasets/records shaped like
the bodp-frontend prototype's mock data (Master Plan §3 Phase 3 task 6),
but landed as genuine rows via the real ORM models, not JS mock objects.

Usage:
    python -m app.scripts.seed_catalog [--reset]

--reset wipes existing stations/categories/datasets/records first so the
script is safely re-runnable during development.
"""

import argparse
import asyncio
import random
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal, SyncSessionLocal
from app.models.catalog import (
    Dataset,
    DatasetCategory,
    DatasetRecord,
    DatasetStatus,
    QualityFlag,
    Station,
)
from app.services.dataset_file_service import upload_dataset_file
from app.worker.tasks.ingestion import process_dataset_file

# Maps a seeded dataset's code to its real sample file in data_repository/ —
# uploaded through the exact same pipeline a browser upload uses (Master
# Plan §3 Phase 5), so seeded datasets have genuine DatasetFile rows for
# extraction to operate on. Not every seeded dataset has a matching sample
# file yet; datasets without an entry here simply get no DatasetFile (their
# catalog listing/preview still works off the synthetic DatasetRecord rows
# below — only extraction needs the real file).
_SAMPLE_FILE_BY_CODE = {
    "BD-SST-2022-2024": "data_repository/csv/bay_of_bengal_sst_2022_2024.csv",
    "BD-WND-2023-2024": "data_repository/netcdf/coastal_wind_precip_2023_2024.nc",
    "BD-SAL-SDB-2024": "data_repository/mat/sundarbans_salinity_2024.mat",
    "BD-MOD-WAVE-2024": "data_repository/geotiff/modeled_wave_height_2024.tif",
}


class _AsyncFileReader:
    """Wraps a local file so it satisfies upload_dataset_file()'s expected
    `file_stream.read(size) -> bytes` async interface — the real service
    only ever sees a FastAPI UploadFile in production, but its read
    interface is this simple, so a thin wrapper is enough for seeding."""

    def __init__(self, path: Path):
        self._f = open(path, "rb")

    async def read(self, size: int) -> bytes:
        return self._f.read(size)

    def close(self) -> None:
        self._f.close()


async def _seed_real_file(dataset_id, code: str, repo_root: Path) -> None:
    rel_path = _SAMPLE_FILE_BY_CODE.get(code)
    if rel_path is None:
        return
    path = repo_root / rel_path
    if not path.exists():
        print(f"  Skipping real file for {code}: {path} not found")
        return

    async with AsyncSessionLocal() as db:
        reader = _AsyncFileReader(path)
        try:
            upload, dataset_file = await upload_dataset_file(
                db,
                dataset_id=dataset_id,
                filename=path.name,
                file_stream=reader,
                uploaded_by=None,
            )
        finally:
            reader.close()

    # Synchronous direct call (not .delay()) — a seed script has no running
    # Celery worker guarantee and wants the dataset fully ingested before
    # returning, exactly like eager-mode Celery test config already does.
    process_dataset_file(str(dataset_file.id), str(upload.id))
    print(f"  Uploaded + ingested real file for {code}: {path.name}")

# Real Bangladesh coastal/estuarine monitoring station coordinates, matching
# bodp-frontend/src/lib/mock-data/stations.ts exactly so ported frontend
# fixtures/screenshots line up with real seeded data.
STATIONS = [
    ("Cox's Bazar", "ST-COXB", 21.4272, 92.0058, 12),
    ("Chittagong", "ST-CTG", 22.3569, 91.7832, 18),
    ("Sundarbans", "ST-SDB", 21.9497, 89.1833, 8),
    ("Meghna Estuary", "ST-MGE", 22.5, 90.8, 15),
    ("Kutubdia", "ST-KTB", 21.8167, 91.85, 22),
    ("Teknaf", "ST-TKF", 20.8625, 92.3025, 25),
    ("Patenga", "ST-PTG", 22.2371, 91.7905, 10),
    ("Saint Martin", "ST-SMT", 20.6272, 92.3217, 30),
    ("Mongla", "ST-MGL", 22.4833, 89.5833, 9),
    ("Char Fasson", "ST-CHF", 22.1833, 90.75, 11),
    ("Hatiya", "ST-HTY", 22.4, 91.0833, 13),
    ("Sandwip", "ST-SDW", 22.5333, 91.4167, 14),
    ("Feni", "ST-FNI", 22.9333, 91.4, 7),
    ("Noakhali", "ST-NKL", 22.85, 91.0667, 6),
    ("Bhola", "ST-BHL", 22.6833, 90.65, 10),
    ("Barisal", "ST-BRS", 22.701, 90.3535, 8),
    ("Khepupara", "ST-KHP", 21.9833, 90.2167, 16),
    ("Patuakhali", "ST-PTK", 22.3596, 90.3296, 9),
]

CATEGORIES = [
    ("Environmental", "cat-Environmental", "Sea surface conditions, temperature, and salinity"),
    ("Pollution", "cat-Pollution", "Microplastics, heavy metals, and industrial effluent"),
    ("Water Quality", "cat-Water", "Turbidity, nutrients, and microbial water quality"),
    ("Hydrological", "cat-Hydrological", "River discharge, tidal, and estuarine dynamics"),
    ("Atmospheric", "cat-Atmospheric", "Wind, precipitation, and atmospheric pressure"),
    ("Model Data", "cat-Model", "Gridded/modeled outputs and reanalysis products"),
]

SOURCES = ["BORI Station", "Coastal Survey", "Research Vessel", "Satellite Feed", "Partner Institution"]
PLATFORMS = ["Fixed Buoy", "Satellite", "Research Vessel", "Coastal Station", "Autonomous Glider", "Drifting Buoy"]
RESOLUTIONS = ["Hourly", "Daily", "Weekly", "Monthly", "Seasonal"]
PROCESSING_LEVELS = ["L0 — Raw", "L1 — Calibrated", "L2 — Derived", "L3 — Gridded/Modeled"]
FORMATS = ["CSV", "NetCDF", "Excel", ".mat", "GeoTIFF"]

PARAMS_BY_CATEGORY = {
    "Environmental": [("Sea Surface Temp", "°C", 27.0), ("Salinity", "PSU", 33.5)],
    "Pollution": [("Microplastics", "particles/m³", 4.2), ("Heavy Metals (Pb)", "µg/L", 2.1)],
    "Water Quality": [("Turbidity", "NTU", 15.0), ("Nitrate", "mg/L", 0.8), ("Coliform Count", "CFU/100mL", 120.0)],
    "Hydrological": [("River Discharge", "m³/s", 4500.0), ("Tidal Range", "m", 2.3)],
    "Atmospheric": [("Wind Speed", "m/s", 6.5), ("Precipitation", "mm", 8.0)],
    "Model Data": [("Modeled SST", "°C", 27.5), ("Wave Height", "m", 1.4)],
}

DATASETS_SPEC = [
    ("BD-SST-2022-2024", "Bay of Bengal SST 2022–2024", "Environmental",
     "Bay of Bengal (all stations)",
     "Monthly sea surface temperature and salinity measurements across 18 coastal and offshore monitoring stations, 2022–2024.",
     820),
    ("BD-MPL-CXB-2024", "Cox's Bazar Microplastics Q3 2024", "Pollution",
     "Cox's Bazar",
     "Coastal water microplastic particle counts and trace heavy-metal concentrations, Cox's Bazar baseline survey.",
     240),
    ("BD-HM-CXB-2022", "Chittagong Heavy Metals Baseline", "Pollution",
     "Chittagong",
     "Baseline survey of heavy metal contamination near Chittagong port industrial zones.",
     180),
    ("BD-WQ-2020-2024", "Water Quality Dataset (2020–2024)", "Water Quality",
     "Multiple stations",
     "Multi-year water quality monitoring covering turbidity, nutrients, and coliform counts nationwide.",
     530),
    ("BD-SAL-SDB-2024", "Sundarbans Salinity Dynamics", "Hydrological",
     "Sundarbans",
     "Tidal and salinity dynamics across the Sundarbans mangrove estuary system.",
     310),
    ("BD-WND-2023-2024", "Coastal Wind & Precipitation 2023–2024", "Atmospheric",
     "Coastal stations (all)",
     "Wind speed and precipitation records from coastal monitoring stations along the Bay of Bengal.",
     410),
    ("BD-MOD-WAVE-2024", "Modeled Wave Height Reanalysis 2024", "Model Data",
     "Bay of Bengal (gridded)",
     "Gridded modeled wave height and modeled SST reanalysis product for the Bay of Bengal.",
     265),
    ("BD-RIV-MGE-2021-2024", "Meghna Estuary River Discharge", "Hydrological",
     "Meghna Estuary",
     "River discharge and tidal range measurements at the Meghna river estuary, 2021–2024.",
     395),
]


def _bbox_wkt(lat_min, lat_max, lon_min, lon_max) -> str:
    return (
        f"SRID=4326;POLYGON(("
        f"{lon_min} {lat_min}, {lon_max} {lat_min}, "
        f"{lon_max} {lat_max}, {lon_min} {lat_max}, "
        f"{lon_min} {lat_min}))"
    )


def seed(reset: bool = False) -> None:
    seeded_dataset_ids: list[tuple] = []

    with SyncSessionLocal() as db:
        if reset:
            db.execute(delete(DatasetRecord))
            db.execute(delete(Dataset))
            db.execute(delete(DatasetCategory))
            db.execute(delete(Station))
            db.commit()
            print("Cleared existing catalog data.")

        # --- Stations ---
        station_rows: dict[str, Station] = {}
        for name, code, lat, lon, depth in STATIONS:
            existing = db.query(Station).filter(Station.code == code).one_or_none()
            if existing:
                station_rows[code] = existing
                continue
            station = Station(name=name, code=code, lat=lat, lon=lon, depth_m=depth)
            db.add(station)
            station_rows[code] = station
        db.commit()
        print(f"Seeded {len(station_rows)} stations.")

        # --- Categories ---
        category_rows: dict[str, DatasetCategory] = {}
        for name, color_tag, description in CATEGORIES:
            existing = db.query(DatasetCategory).filter(DatasetCategory.name == name).one_or_none()
            if existing:
                category_rows[name] = existing
                continue
            category = DatasetCategory(name=name, description=description, color_tag=color_tag)
            db.add(category)
            category_rows[name] = category
        db.commit()
        print(f"Seeded {len(category_rows)} categories.")

        # --- Datasets + synthetic records ---
        rng = random.Random(42)  # deterministic across re-runs
        station_list = list(station_rows.values())
        total_records = 0

        for code, title, category_name, location, description, record_count in DATASETS_SPEC:
            existing_ds = db.query(Dataset).filter(Dataset.code == code).one_or_none()
            if existing_ds:
                print(f"  Skipping {code} (already exists)")
                continue

            params = PARAMS_BY_CATEGORY[category_name]
            param_names = [p[0] for p in params]
            n_platforms = rng.randint(2, 3)
            platforms = rng.sample(PLATFORMS, n_platforms)
            n_formats = rng.randint(1, 3)
            formats = rng.sample(FORMATS, n_formats)
            n_levels = rng.randint(1, 3)
            processing_levels = PROCESSING_LEVELS[:n_levels]
            source = rng.choice(SOURCES)
            resolution = rng.choice(RESOLUTIONS)

            dataset = Dataset(
                code=code,
                title=title,
                description=description,
                category_id=category_rows[category_name].id,
                location=location,
                source=source,
                platforms=platforms,
                parameters=param_names,
                resolution=resolution,
                license="CC BY 4.0",
                processing_levels=processing_levels,
                formats=formats,
                status=DatasetStatus.PUBLISHED.value,
                record_count=0,  # accumulated below as real records are added
            )
            db.add(dataset)
            db.flush()

            # Synthetic records: spread over the last ~2 years, jittered
            # around a random subset of stations, seasonally-perturbed values
            # (deterministic per-dataset seed so re-runs are reproducible).
            n_records = record_count // 4  # keep local dev DB lightweight
            start_date = date(2023, 1, 1)
            lat_min = lat_max = lon_min = lon_max = None
            temporal_start = temporal_end = None

            dataset_stations = rng.sample(station_list, min(len(station_list), rng.randint(4, 8)))

            for i in range(n_records):
                station = rng.choice(dataset_stations)
                param_name, unit, base_value = rng.choice(params)
                day_offset = rng.randint(0, 730)
                record_date = start_date + timedelta(days=day_offset)
                lat_jitter = station.lat + rng.uniform(-0.05, 0.05)
                lon_jitter = station.lon + rng.uniform(-0.05, 0.05)
                value = round(base_value + rng.uniform(-base_value * 0.15, base_value * 0.15), 3)
                depth = round(max(0.5, station.depth_m + rng.uniform(-2, 2)), 1) if station.depth_m else None

                roll = rng.random()
                quality = (
                    QualityFlag.ALERT.value
                    if roll < 0.07
                    else QualityFlag.CAUTION.value
                    if roll < 0.20
                    else QualityFlag.NORMAL.value
                )

                record = DatasetRecord(
                    dataset_id=dataset.id,
                    time=record_date,
                    lat=lat_jitter,
                    lon=lon_jitter,
                    depth_m=depth,
                    location=station.name,
                    station_id=station.id,
                    parameter=param_name,
                    value=value,
                    unit=unit,
                    quality_flag=quality,
                    processing_level=rng.choice(processing_levels),
                    format=rng.choice(formats),
                    source=source,
                    platform=rng.choice(platforms),
                    geom=f"SRID=4326;POINT({lon_jitter} {lat_jitter})",
                )
                db.add(record)

                lat_min = lat_jitter if lat_min is None else min(lat_min, lat_jitter)
                lat_max = lat_jitter if lat_max is None else max(lat_max, lat_jitter)
                lon_min = lon_jitter if lon_min is None else min(lon_min, lon_jitter)
                lon_max = lon_jitter if lon_max is None else max(lon_max, lon_jitter)
                temporal_start = record_date if temporal_start is None else min(temporal_start, record_date)
                temporal_end = record_date if temporal_end is None else max(temporal_end, record_date)

            dataset.record_count = n_records
            dataset.temporal_start = temporal_start
            dataset.temporal_end = temporal_end
            if lat_min is not None:
                dataset.spatial_extent = _bbox_wkt(lat_min, lat_max, lon_min, lon_max)

            db.commit()
            total_records += n_records
            seeded_dataset_ids.append((dataset.id, code))
            print(f"  Seeded {code}: {n_records} records")

        print(f"Done. {total_records} total records across {len(DATASETS_SPEC)} datasets.")

    # Real-file upload runs after the sync session above closes/commits —
    # needs its own async session, and only makes sense for datasets that
    # were actually (re)created this run (skipped ones already have
    # whatever DatasetFile state a prior run left them in).
    codes_with_samples = [(did, code) for did, code in seeded_dataset_ids if code in _SAMPLE_FILE_BY_CODE]
    if codes_with_samples:
        repo_root = Path(__file__).resolve().parent.parent.parent
        print(f"Uploading {len(codes_with_samples)} real sample file(s) through the ingestion pipeline...")

        async def _upload_all():
            for dataset_id, code in codes_with_samples:
                await _seed_real_file(dataset_id, code, repo_root)

        asyncio.run(_upload_all())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="Clear existing catalog data first")
    args = parser.parse_args()
    seed(reset=args.reset)
