from pathlib import Path

import numpy as np
import rasterio
from rasterio.errors import RasterioIOError
from rasterio.warp import transform_bounds

from app.services.parsers.base import (
    DataShape,
    FileParser,
    ParsedFileMetadata,
    ParserError,
    ProcessedArtifact,
)

# EPSG:4326 (WGS84 lat/lon) — the CRS every other parser's extent fields are
# expressed in and PostGIS spatial_extent columns are stored as (SRID 4326).
# Rasters may arrive in any projected CRS; bounds are reprojected to this one
# so GeoTIFF extent detection is directly comparable to CSV/NetCDF/.mat.
_TARGET_CRS = "EPSG:4326"

# Below this pixel count, GDAL's default COG overview thresholds mean no
# overview levels get built — harmless (the raster is small enough that
# overviews wouldn't help query performance anyway), not an error condition.
_MIN_PIXELS_FOR_OVERVIEWS = 512 * 512


class GeoTiffParser(FileParser):
    """Cloud-Optimized GeoTIFF support (raster data — imagery, gridded model
    output, bathymetry, etc., as opposed to the point/tabular observations
    CSV/NetCDF/.mat represent).

    Accepts both plain GeoTIFFs and already-COG GeoTIFFs as input; the
    processed/ artifact is always re-written as a proper COG (internal
    tiling + overviews) via the 'COG' GDAL driver, regardless of whether the
    upload already was one — re-tiling is cheap and guarantees the stored
    processed copy always has consistent, known tiling/overview settings
    rather than depending on whatever the uploader's tool produced.
    """

    extensions = ("tif", "tiff", "geotiff")
    data_shape = DataShape.RASTER

    def parse(self, path: Path) -> ParsedFileMetadata:
        try:
            with rasterio.open(path) as src:
                return self._extract(src)
        except ParserError:
            raise
        except RasterioIOError as exc:
            raise ParserError(f"File is not a valid GeoTIFF: {exc}") from exc
        except Exception as exc:
            raise ParserError(f"Failed to read GeoTIFF: {exc}") from exc

    def _extract(self, src) -> ParsedFileMetadata:
        if src.count == 0:
            raise ParserError("GeoTIFF has no raster bands")
        if src.crs is None:
            raise ParserError("GeoTIFF has no coordinate reference system (CRS) defined")

        try:
            lon_min, lat_min, lon_max, lat_max = transform_bounds(
                src.crs, _TARGET_CRS, *src.bounds
            )
        except Exception as exc:
            raise ParserError(f"Failed to reproject raster bounds to EPSG:4326: {exc}") from exc

        band_names = [
            (src.descriptions[i] or f"band_{i + 1}") for i in range(src.count)
        ]

        return ParsedFileMetadata(
            shape=DataShape.RASTER,
            bands=band_names,
            pixel_width=src.width,
            pixel_height=src.height,
            spatial_lat_min=float(lat_min),
            spatial_lat_max=float(lat_max),
            spatial_lon_min=float(lon_min),
            spatial_lon_max=float(lon_max),
            extra={
                "crs": str(src.crs),
                "dtype": str(src.dtypes[0]),
                "nodata": src.nodata,
                "driver": src.driver,
            },
        )

    def to_processed(self, path: Path, output_dir: Path) -> ProcessedArtifact:
        output_path = output_dir / "processed.cog.tif"
        try:
            with rasterio.open(path) as src:
                if src.count == 0:
                    raise ParserError("GeoTIFF has no raster bands to convert")

                profile = src.profile.copy()
                profile.update(
                    driver="COG",
                    compress="DEFLATE",
                    overview_resampling="average",
                )
                # COG driver manages tiling/overviews itself — these
                # plain-GTiff-specific keys aren't meaningful to it and can
                # cause a spurious creation-option warning if left in.
                profile.pop("tiled", None)
                profile.pop("blockxsize", None)
                profile.pop("blockysize", None)

                data = src.read()

                with rasterio.open(output_path, "w", **profile) as dst:
                    dst.write(data)
                    for i in range(1, src.count + 1):
                        desc = src.descriptions[i - 1]
                        if desc:
                            dst.set_band_description(i, desc)

            return ProcessedArtifact(
                local_path=output_path,
                content_type="image/tiff; application=geotiff; profile=cloud-optimized",
                file_extension="cog.tif",
                row_count=None,
            )
        except ParserError:
            raise
        except Exception as exc:
            raise ParserError(f"Failed to convert GeoTIFF to Cloud-Optimized GeoTIFF: {exc}") from exc
