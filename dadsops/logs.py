"""Per-tool log files: a safe path inside the log directory and the last lines of it."""

from __future__ import annotations

import os
import re
from pathlib import Path

TAIL_LINES = 200
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def log_file(log_dir: Path, name: str) -> Path:
    """<log_dir>/<name>.log; refuses any name that could point outside log_dir."""
    if not SAFE_NAME_RE.match(name):
        raise ValueError(f"invalid tool name for a log file: {name!r}")
    log_dir = Path(log_dir).resolve()
    path = (log_dir / f"{name}.log").resolve()
    if path.parent != log_dir:
        raise ValueError(f"log file for {name!r} would leave the log directory")
    return path


def tail(path: Path, lines: int = TAIL_LINES, block: int = 8192) -> list[str]:
    """The last `lines` lines of path, reading backwards; [] when there is no file."""
    try:
        fh = path.open("rb")
    except FileNotFoundError:
        return []
    with fh:
        end = fh.seek(0, os.SEEK_END)
        data = b""
        while end > 0 and data.count(b"\n") <= lines:
            start = max(0, end - block)
            fh.seek(start)
            data = fh.read(end - start) + data
            end = start
    return data.decode("utf-8", errors="replace").splitlines()[-lines:]
