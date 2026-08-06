"""One-off dev tool: generates the realistic sample scientific files that
live in backend/data_repository/ and get uploaded through the real ingestion
pipeline by app/scripts/seed_catalog.py (Master Plan §3 Phase 5).

Not part of the deployed app and not imported by any runtime code — run
manually whenever the sample set needs regenerating:

    python scripts/generate_sample_data_repository.py

Swapping in real production data later means only replacing the files this
script writes (or, from Phase 8 onward, registering real objects directly)
— no pipeline code changes.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import scipy.io
import xarray as xr

ROOT = Path(__file__).resolve().parent.parent / "data_repository"

_STATIONS = [
    ("Cox's Bazar", "ST-COXB", 21.4272, 92.0058),
    ("Chittagong", "ST-CTG", 22.3569, 91.7832),
    ("Sundarbans", "ST-SDB", 21.9497, 89.1833),
    ("Meghna Estuary", "ST-MGE", 22.5, 90.8),
    ("Khepupara", "ST-KHP", 21.9833, 90.2167),
]


def _write_csv() -> None:
    """BD-SST-2022-2024 — Bay of Bengal sea surface temperature + salinity."""
    rng = np.random.default_rng(42)
    rows = []
    dates = pd.date_range("2022-01-01", "2024-12-01", freq="MS")
    for d in dates:
        for name, code, lat, lon in _STATIONS:
            rows.append(
                {
                    "time": d.date().isoformat(),
                    "lat": round(lat + rng.uniform(-0.05, 0.05), 4),
                    "lon": round(lon + rng.uniform(-0.05, 0.05), 4),
                    "station": code,
                    "sea_surface_temp": round(26.5 + 2 * np.sin(d.month / 12 * 2 * np.pi) + rng.uniform(-0.3, 0.3), 2),
                    "salinity": round(33.0 + rng.uniform(-1.0, 1.0), 2),
                }
            )
    df = pd.DataFrame(rows)
    out = ROOT / "csv" / "bay_of_bengal_sst_2022_2024.csv"
    df.to_csv(out, index=False)
    print(f"wrote {out} ({len(df)} rows)")


def _write_netcdf() -> None:
    """BD-WND-2023-2024 — coastal wind speed + precipitation, point time series."""
    rng = np.random.default_rng(7)
    times = pd.date_range("2023-01-01", "2024-12-01", freq="MS")
    station_names = [s[1] for s in _STATIONS]
    lat = np.array([s[2] for s in _STATIONS])
    lon = np.array([s[3] for s in _STATIONS])

    wind_speed = 5.5 + rng.uniform(-2, 2, size=(len(times), len(station_names)))
    precipitation = np.clip(8.0 + rng.uniform(-6, 12, size=(len(times), len(station_names))), 0, None)

    ds = xr.Dataset(
        data_vars={
            "wind_speed": (("time", "station"), wind_speed),
            "precipitation": (("time", "station"), precipitation),
        },
        coords={
            "time": times,
            "station": station_names,
            "lat": ("station", lat),
            "lon": ("station", lon),
        },
        attrs={"title": "Coastal Wind & Precipitation 2023-2024", "source": "BODP sample data repository"},
    )
    out = ROOT / "netcdf" / "coastal_wind_precip_2023_2024.nc"
    ds.to_netcdf(out)
    print(f"wrote {out} ({ds.sizes})")


def _write_mat() -> None:
    """BD-SAL-SDB-2024 — Sundarbans salinity dynamics."""
    rng = np.random.default_rng(11)
    n = 240
    # MATLAB datenum: days since year 0 (proleptic), matching mat_parser.py's
    # `time - 719529` conversion back to a Python/pandas timestamp.
    start = pd.Timestamp("2024-01-01")
    dates = pd.date_range(start, periods=n, freq="6h")
    datenum = (dates - pd.Timestamp("1970-01-01")).days.to_numpy() + 719529
    datenum = datenum.astype("float64") + ((dates - pd.Timestamp("1970-01-01")).seconds.to_numpy() / 86400.0)

    lat = 21.9497 + rng.uniform(-0.08, 0.08, size=n)
    lon = 89.1833 + rng.uniform(-0.08, 0.08, size=n)
    salinity = 18.0 + 3.0 * np.sin(np.linspace(0, 8 * np.pi, n)) + rng.uniform(-0.5, 0.5, size=n)
    tidal_range = 1.8 + rng.uniform(-0.4, 0.4, size=n)

    out = ROOT / "mat" / "sundarbans_salinity_2024.mat"
    scipy.io.savemat(
        out,
        {
            "time": datenum,
            "lat": lat,
            "lon": lon,
            "salinity": salinity,
            "tidal_range": tidal_range,
        },
    )
    print(f"wrote {out} ({n} rows)")


def _write_geotiff() -> None:
    """BD-MOD-WAVE-2024 — modeled wave height reanalysis, gridded raster."""
    import rasterio
    from rasterio.transform import from_origin

    width, height = 120, 90
    rng = np.random.default_rng(23)
    base = np.linspace(0.5, 2.5, height).reshape(-1, 1) * np.ones((1, width))
    data = (base + rng.uniform(-0.2, 0.2, size=(height, width))).astype("float32")

    out = ROOT / "geotiff" / "modeled_wave_height_2024.tif"
    transform = from_origin(88.0, 23.0, 0.02, 0.02)
    with rasterio.open(
        out, "w", driver="GTiff", height=height, width=width, count=1,
        dtype="float32", crs="EPSG:4326", transform=transform,
    ) as dst:
        dst.write(data, 1)
        dst.set_band_description(1, "wave_height_m")
    print(f"wrote {out} ({width}x{height})")


if __name__ == "__main__":
    for sub in ("csv", "netcdf", "mat", "geotiff"):
        (ROOT / sub).mkdir(parents=True, exist_ok=True)
    _write_csv()
    _write_netcdf()
    _write_mat()
    _write_geotiff()
