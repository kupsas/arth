"""
Temporary workspace for multipart portfolio uploads (Phase A.3).

Parsers expect a file path or a directory path on disk. We stream uploaded
bytes into a throwaway folder, run the parser, then delete the folder.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException, UploadFile


@contextmanager
def saved_upload_directory(files: list[UploadFile]) -> Iterator[Path]:
    """Write every upload under a fresh temp directory; yield that path; always cleanup."""
    if not files:
        raise HTTPException(status_code=400, detail="At least one file is required")
    td = Path(tempfile.mkdtemp(prefix="arth_portfolio_ingest_"))
    try:
        for uf in files:
            raw_name = uf.filename or "upload.csv"
            # Prevent path traversal — keep only the final segment.
            safe_name = Path(raw_name).name
            if not safe_name or safe_name in (".", ".."):
                raise HTTPException(status_code=400, detail=f"Invalid filename: {raw_name!r}")
            dest = td / safe_name
            body = uf.file.read()
            if len(body) > 50 * 1024 * 1024:
                raise HTTPException(status_code=413, detail="Single upload exceeds 50 MB limit")
            dest.write_bytes(body)
        yield td
    finally:
        shutil.rmtree(td, ignore_errors=True)


def parser_input_path(temp_dir: Path) -> Path:
    """If exactly one file was uploaded, parsers may use the file path; else the directory."""
    entries = [p for p in temp_dir.iterdir() if p.is_file()]
    if len(entries) == 1:
        return entries[0]
    return temp_dir
