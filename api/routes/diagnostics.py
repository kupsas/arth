"""
Diagnostics API — download packs of local log files for support.

GET /api/diagnostics/logs  — ZIP of ``data/logs`` artefacts (auth required).

Only known filenames are bundled so we never accidentally zip unrelated files if
that directory grows new contents later.
"""

from __future__ import annotations

import io
import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from api.auth import get_current_user
from pipeline.logging_config import get_log_file_path

logger = logging.getLogger(__name__)

router = APIRouter()

# Human-readable note inside the archive when no logs exist yet (first install / cleared folder).
_EMPTY_ZIP_README = """No log files were found yet.

Use Arth for a bit — sync mail, run an import, anything that writes to the log — then try again. You can share this zip with support when something looks off.
"""


def _collect_log_file_paths() -> list[Path]:
    """Return an ordered list of log files to bundle into the ZIP.

    We only ever add:
      - The rotating app log and its backups (``arth.log``, ``arth.log.1``, …).
      - ``email-import.log`` when the onboarding email-import path has run.

    Paths are absolute and checked with ``is_file()`` so missing rotations are skipped.
    """

    main_log = get_log_file_path()
    log_dir = main_log.parent
    candidates: list[Path] = []

    # Primary rotating log — present once anything has been written at DEBUG+.
    if main_log.is_file():
        candidates.append(main_log)

    # RotatingFileHandler renames full files to .1 … .5 (see pipeline/logging_config.py).
    for i in range(1, 6):
        rotated = log_dir / f"arth.log.{i}"
        if rotated.is_file():
            candidates.append(rotated)

    email_import = log_dir / "email-import.log"
    if email_import.is_file():
        candidates.append(email_import)

    return candidates


@router.get("/logs")
def download_local_logs_zip(_user: object = Depends(get_current_user)) -> Response:
    """Build a ZIP of local diagnostic logs and return it as a download.

    Requires a valid session — same as other settings APIs. The browser should
    save the file; filenames are safe ASCII for Content-Disposition.
    """

    candidates = _collect_log_file_paths()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        if not candidates:
            # Still return a useful archive so the button never "does nothing" confusingly.
            zf.writestr("README.txt", _EMPTY_ZIP_README)
        else:
            for path in candidates:
                # Arcname = basename only — keeps the zip flat and avoids leaking host paths.
                zf.write(path, arcname=path.name)

    payload = buf.getvalue()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"arth-logs-{stamp}.zip"

    logger.info(
        "Diagnostic log bundle ready (%s bytes · %s files)",
        len(payload),
        len(candidates),
    )

    return Response(
        content=payload,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
