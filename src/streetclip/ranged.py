"""HTTP Range support for serving video.

Starlette's FileResponse sends the whole file with a 200, which means a browser
cannot seek: scrubbing to 40 minutes into a two-hour recording would download
the first 40 minutes to get there. The review UI is built on seeking, so range
requests are not optional here.
"""

from __future__ import annotations

import mimetypes
import re
from collections.abc import Iterator
from pathlib import Path

from starlette.responses import FileResponse, Response, StreamingResponse

CHUNK_SIZE = 512 * 1024

RANGE_PATTERN = re.compile(r"bytes=(\d*)-(\d*)")


def parse_range(header: str, file_size: int) -> tuple[int, int] | None:
    """Parse a single-range `bytes=` header into inclusive (start, end).

    Returns None when the header is absent, malformed, or asks for more than
    one range — callers fall back to sending the whole file, which is a valid
    response to any range request.
    """
    if not header:
        return None

    match = RANGE_PATTERN.fullmatch(header.strip())
    if match is None:
        return None

    raw_start, raw_end = match.groups()

    if raw_start == "":
        if raw_end == "":
            return None
        # `bytes=-500` means the final 500 bytes.
        length = min(int(raw_end), file_size)
        if length <= 0:
            return None
        return file_size - length, file_size - 1

    start = int(raw_start)
    end = int(raw_end) if raw_end else file_size - 1
    end = min(end, file_size - 1)

    if start > end or start >= file_size:
        return None
    return start, end


def _read_range(path: Path, start: int, end: int) -> Iterator[bytes]:
    remaining = end - start + 1
    with path.open("rb") as handle:
        handle.seek(start)
        while remaining > 0:
            chunk = handle.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def ranged_file_response(path: Path, range_header: str, filename: str | None = None) -> Response:
    """Serve `path`, honouring a Range header when one is present and valid."""
    file_size = path.stat().st_size
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    requested = parse_range(range_header, file_size)
    if requested is None:
        response = FileResponse(path, media_type=media_type, filename=filename)
        # Advertise range support so the browser knows it may seek next time.
        response.headers["Accept-Ranges"] = "bytes"
        return response

    start, end = requested
    return StreamingResponse(
        _read_range(path, start, end),
        status_code=206,
        media_type=media_type,
        headers={
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(end - start + 1),
            "Accept-Ranges": "bytes",
        },
    )
