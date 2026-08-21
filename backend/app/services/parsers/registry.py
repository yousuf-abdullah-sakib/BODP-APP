from pathlib import Path

from app.services.parsers.base import FileParser, ParserError
from app.services.parsers.csv_parser import CsvParser
from app.services.parsers.geotiff_parser import GeoTiffParser
from app.services.parsers.mat_parser import MatParser
from app.services.parsers.netcdf_parser import NetcdfParser

# Extensible registry (Master Plan §3 Phase 2 task 3: "extensible registry
# for future formats like GeoTIFF, generic HDF5"). Adding a format means
# adding one FileParser subclass and one line here — nothing else in the
# ingestion pipeline needs to change (demonstrated by GeoTIFF's addition
# after the initial CSV/NetCDF/.mat set).
#
# Zarr is NOT in this list — see the FileParser docstring in base.py for why
# it needs an upload/storage-layer change first, not just a parser.
_PARSERS: list[FileParser] = [CsvParser(), NetcdfParser(), MatParser(), GeoTiffParser()]

_EXTENSION_MAP: dict[str, FileParser] = {
    ext: parser for parser in _PARSERS for ext in parser.extensions
}

# Magic-byte signatures used to verify the file's real content matches its
# declared extension (Master Plan §3 Phase 2 task 6: "reject mismatched
# extension vs. actual content"). Not exhaustive — just enough to catch the
# common case of a renamed/misidentified file.
_MAGIC_SIGNATURES: dict[str, bytes] = {
    "nc": b"CDF",  # classic NetCDF; v4 NetCDF is really HDF5 (see below)
}


def get_parser_for_format(extension: str) -> FileParser:
    ext = extension.lower().lstrip(".")
    parser = _EXTENSION_MAP.get(ext)
    if parser is None:
        raise ParserError(f"No parser registered for file format: {extension!r}")
    return parser


def sniff_format(path: Path, declared_extension: str) -> str:
    """Verify the file's actual bytes are consistent with its declared
    extension. Returns the (possibly normalized) format string on success,
    raises ParserError on a clear mismatch.

    NetCDF-4 and MATLAB v7.3 are both HDF5 containers, and MATLAB <v7.3 has
    its own distinct magic header — we check what we can cheaply and defer
    the rest to the parser itself, which will raise ParserError anyway if
    the content doesn't actually parse as claimed.
    """
    ext = declared_extension.lower().lstrip(".")
    if ext not in _EXTENSION_MAP:
        raise ParserError(f"Unsupported file format: {declared_extension!r}")

    try:
        with open(path, "rb") as f:
            header = f.read(8)
    except OSError as exc:
        raise ParserError(f"Could not read file to verify format: {exc}") from exc

    is_hdf5 = header[:8] == b"\x89HDF\r\n\x1a\n"
    is_classic_netcdf = header[:3] == b"CDF"
    is_legacy_mat = header[:4] == b"MATL" or b"MATLAB" in header
    # TIFF magic: "II*\x00" (little-endian) or "MM\x00*" (big-endian).
    # GeoTIFF is a plain TIFF with extra georeferencing tags, so the magic
    # bytes are identical — genuine validation happens in the parser itself.
    is_tiff = header[:4] in (b"II*\x00", b"MM\x00*")

    if ext in ("nc", "netcdf", "nc4") and not (is_hdf5 or is_classic_netcdf):
        raise ParserError(
            f"File extension is .{ext} but content is not a recognized NetCDF format"
        )
    if ext == "mat" and not (is_hdf5 or is_legacy_mat):
        raise ParserError("File extension is .mat but content is not a recognized MATLAB format")
    if ext == "csv" and (is_hdf5 or is_classic_netcdf or is_tiff):
        raise ParserError("File extension is .csv but content looks like a binary format")
    if ext == "csv":
        # CSV is the only text-based format in this registry — unlike the
        # NetCDF/mat/TIFF checks above (which match against a specific
        # known-good signature), a mismatched CSV upload isn't limited to
        # a handful of other scientific formats we recognize. A renamed
        # arbitrary binary (e.g. a PE/ELF executable) has no signature in
        # the checks above and would otherwise reach the parser itself
        # before being rejected (Phase 10 security audit finding: the
        # parser DOES still reject it — pd.read_csv fails to UTF-8-decode
        # binary bytes — so this was never an ingestion bypass, just a
        # later, more expensive rejection point than intended). Restores
        # the "reject before any parser attempts real work" intent this
        # function's own docstring states, without hardcoding a
        # magic-byte blocklist for every possible non-CSV binary format.
        #
        # Two checks, not one: valid-UTF-8-decodability alone isn't
        # enough — control-character bytes (e.g. ELF's leading \x7f, or
        # embedded \x00 NUL bytes) are technically valid single-byte UTF-8
        # codepoints and would pass a decode-only check while still being
        # clearly not real CSV text (confirmed by testing a genuine ELF
        # header against an earlier, decode-only version of this check —
        # it passed incorrectly). A NUL byte in particular can never
        # legitimately appear in a text CSV.
        try:
            decoded = header.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ParserError(
                "File extension is .csv but content is not valid text"
            ) from exc
        if "\x00" in decoded:
            raise ParserError("File extension is .csv but content is not valid text")
    if ext in ("tif", "tiff", "geotiff") and not is_tiff:
        raise ParserError(f"File extension is .{ext} but content is not a recognized TIFF format")

    return ext
