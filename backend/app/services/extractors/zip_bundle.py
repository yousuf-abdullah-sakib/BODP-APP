import zipfile
from pathlib import Path

from app.services.extractors.base import ExtractionResult


def bundle_as_zip(results: list[ExtractionResult], output_dir: Path, base_name: str = "extract") -> ExtractionResult:
    """Zips multiple per-file ExtractionResults into one output object —
    used when a dataset has more than one DatasetFile (Master Plan §3
    Phase 5 task 2: "multi-file datasets -> zip bundle"). Names each entry
    uniquely even if two source files happen to share a base filename."""
    output_path = output_dir / f"{base_name}.zip"
    total_records = 0
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, result in enumerate(results):
            arcname = f"{i:02d}_{result.local_path.name}"
            zf.write(result.local_path, arcname=arcname)
            total_records += result.record_count or 0

    return ExtractionResult(
        local_path=output_path,
        content_type="application/zip",
        file_extension="zip",
        record_count=total_records,
    )
