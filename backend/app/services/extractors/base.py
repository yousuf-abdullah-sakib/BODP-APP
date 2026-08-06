from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


class ExtractorError(Exception):
    """Raised when a subset extraction cannot be completed — malformed
    source data, an unsupported combination of scope + format, etc. Must
    result in the SubsetExtraction row being marked 'failed' with a clear
    reason, mirroring ParserError's role in the ingestion pipeline."""


@dataclass(frozen=True)
class ExtractionResult:
    """Describes the filtered output file an Extractor produced, plus
    enough about its shape for the extraction task to record sensible
    metadata without needing format-specific knowledge."""

    local_path: Path
    content_type: str
    file_extension: str  # no leading dot, e.g. "csv" or "nc"
    record_count: int | None = None


class Extractor(ABC):
    """Isolated per-output-format extractor (Master Plan §3 Phase 5 task 2).
    The inverse of app.services.parsers.FileParser: instead of turning an
    arbitrary uploaded file into a query-optimized processed/ artifact, an
    Extractor turns an already-ingested dataset's source file(s) into a
    smaller, scope-filtered file in the requested output format.

    Each implementation is self-contained — new output formats slot in by
    adding one Extractor subclass and one registry line, nothing else in
    the extraction pipeline needs to change.
    """

    #: Output format this extractor produces (lowercase, no leading dot) —
    #: used by the registry.
    output_format: str = ""

    @abstractmethod
    def extract(
        self,
        *,
        input_path: Path,
        output_dir: Path,
        scope: dict,
    ) -> ExtractionResult:
        """Read the source file at `input_path`, apply `scope` (a dict
        shaped like SearchCriteriaSchema.model_dump()), and write the
        filtered result into `output_dir`. Must raise ExtractorError (not
        let a raw library exception escape) on anything that prevents a
        correct output from being produced."""
