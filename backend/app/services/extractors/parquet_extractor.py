from pathlib import Path

import pandas as pd

from app.services.extractors.base import Extractor, ExtractionResult, ExtractorError
from app.services.extractors.scope_filter import apply_scope_mask


class ParquetExtractor(Extractor):
    output_format = "parquet"

    def extract(self, *, input_path: Path, output_dir: Path, scope: dict) -> ExtractionResult:
        try:
            df = pd.read_parquet(input_path)
        except Exception as exc:
            raise ExtractorError(f"Failed to read source data: {exc}") from exc

        filtered = apply_scope_mask(df, scope)

        output_path = output_dir / "extract.parquet"
        try:
            filtered.to_parquet(output_path, index=False)
        except Exception as exc:
            raise ExtractorError(f"Failed to write Parquet output: {exc}") from exc

        return ExtractionResult(
            local_path=output_path,
            content_type="application/vnd.apache.parquet",
            file_extension="parquet",
            record_count=len(filtered),
        )
