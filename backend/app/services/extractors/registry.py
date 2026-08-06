from app.services.extractors.base import Extractor, ExtractorError
from app.services.extractors.csv_extractor import CsvExtractor
from app.services.extractors.mat_extractor import MatExtractor
from app.services.extractors.netcdf_extractor import NetcdfExtractor
from app.services.extractors.parquet_extractor import ParquetExtractor

# Extensible registry mirroring app/services/parsers/registry.py's pattern —
# adding an output format means adding one Extractor subclass and one line
# here. GeoTIFF is intentionally not offered as an output format in this
# phase (Master Plan §3 Phase 5 names CSV/Parquet/NetCDF/.mat only).
_EXTRACTORS: dict[str, Extractor] = {
    e.output_format: e for e in (CsvExtractor(), ParquetExtractor(), NetcdfExtractor(), MatExtractor())
}


def get_extractor_for_format(output_format: str) -> Extractor:
    ext = output_format.lower().lstrip(".")
    extractor = _EXTRACTORS.get(ext)
    if extractor is None:
        raise ExtractorError(f"No extractor registered for output format: {output_format!r}")
    return extractor
