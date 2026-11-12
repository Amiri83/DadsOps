"""Where DadsOps finds its files: checkout vs installed, registry, settings, logs.

Standard library only: `python3 -m dadsops` imports this before the venv exists.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_REGISTRY = PACKAGE_DIR / "default_tools.yaml"
USER_CONFIG_DIR = Path("~/.config/dadsops")
DEFAULT_LOG_DIR = "~/.local/state/dadsops/logs"


def checkout_root(package_dir: Path | None = None) -> Path | None:
    """The repo root when running from a source checkout, None when pip-installed."""
    root = (package_dir or PACKAGE_DIR).parent
    if (root / "pyproject.toml").is_file() and (root / "tools.yaml").is_file():
        return root
    return None


def config_path() -> Path:
    return Path(os.environ.get("DADSOPS_CONFIG") or USER_CONFIG_DIR / "config.yaml").expanduser()


def registry_path(explicit: Path | None = None) -> Path:
    """--registry, then $DADSOPS_REGISTRY, then the checkout's tools.yaml, then the user's copy."""
    if explicit is not None:
        return Path(explicit).expanduser()
    if os.environ.get("DADSOPS_REGISTRY"):
        return Path(os.environ["DADSOPS_REGISTRY"]).expanduser()
    root = checkout_root()
    if root is not None:
        return root / "tools.yaml"
    return ensure_user_registry(USER_CONFIG_DIR.expanduser() / "tools.yaml")


def ensure_user_registry(target: Path, default: Path = DEFAULT_REGISTRY) -> Path:
    """Copy the packaged default to target once; an existing file is never overwritten.

    The copy is written to a temp file and hard-linked into place, which fails
    instead of replacing when target already exists, so a concurrent first run
    or a crash mid-write can never leave a truncated registry behind.
    """
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(default.read_bytes())
            fh.flush()
            os.fsync(fh.fileno())
        os.link(tmp, target)
    except FileExistsError:
        pass
    finally:
        Path(tmp).unlink(missing_ok=True)
    return target
