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
                    driver="GTiff",
                    compress="DEFLATE",
                    tiled=True,
                    blockxsize=512,
                    blockysize=512,
                )

                # Phase 2: windowed/blockwise read-and-write instead of a
                # single src.read() that materializes every band's full
                # array in memory at once — the previous approach, and the
                # one thing that made a large (multi-GB) raster certain to
                # OOM regardless of how much RAM was provisioned.
                #
                # Writing straight to a COG-driver output while streaming
                # windows isn't supported (the COG driver needs the whole
                # dataset up front to plan overviews) — so this writes a
                # tiled, compressed plain GeoTIFF window-by-window first,
                # then converts that to a real COG via
                # rasterio.shutil.copy(..., driver="COG"), which itself
                # streams the conversion rather than holding the full
                # array. Peak memory is bounded by one window (or one
                # source block, whichever is larger) at a time, not the
                # whole raster.
                tmp_tiled_path = output_dir / "_tiled_intermediate.tif"
                with rasterio.open(tmp_tiled_path, "w", **profile) as dst:
                    for band_index in range(1, src.count + 1):
                        desc = src.descriptions[band_index - 1]
                        if desc:
                            dst.set_band_description(band_index, desc)

                    for _, window in src.block_windows(1):
                        for band_index in range(1, src.count + 1):
                            block = src.read(band_index, window=window)
                            dst.write(block, band_index, window=window)

            import rasterio.shutil as rio_shutil

            rio_shutil.copy(
                tmp_tiled_path,
                output_path,
                driver="COG",
                compress="DEFLATE",
                overview_resampling="average",
            )
            tmp_tiled_path.unlink(missing_ok=True)

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
