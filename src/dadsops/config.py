"""Shared settings (AWS profile, region, theme) forwarded to every started tool.

Only names are stored: never credentials. Unknown keys are rejected so the file
cannot quietly grow into a secrets store.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import yaml

DEFAULT_PATH = Path("~/.config/dadsops/config.yaml")
THEMES = ("light", "dark")
PROFILE_RE = re.compile(r"^[A-Za-z0-9_.@+=,-]{0,64}$")
REGION_RE = re.compile(r"^([a-z]{2}(-[a-z]+)+-[0-9])?$")


class ConfigError(ValueError):
    """The settings file or a submitted value is invalid."""


@dataclass(frozen=True)
class Settings:
    aws_profile: str = ""
    aws_region: str = "us-east-1"
    theme: str = "light"

    def __post_init__(self) -> None:
        if not isinstance(self.aws_profile, str) or not PROFILE_RE.match(self.aws_profile):
            raise ConfigError("AWS profile may use letters, digits and _.@+=,- (max 64)")
        if not isinstance(self.aws_region, str) or not REGION_RE.match(self.aws_region):
            raise ConfigError("AWS region must look like us-east-1 (or be empty)")
        if self.theme not in THEMES:
            raise ConfigError(f"theme must be one of: {', '.join(THEMES)}")


def config_path() -> Path:
    return Path(os.environ.get("DADSOPS_CONFIG") or DEFAULT_PATH).expanduser()


def load_settings(path: Path) -> Settings:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Settings()
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc.strerror or exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    if data is None:
        return Settings()
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must be a mapping of settings")
    unknown = sorted(set(data) - {f.name for f in fields(Settings)})
    if unknown:
        raise ConfigError(f"{path}: unknown setting(s): {', '.join(map(str, unknown))}")
    return Settings(**data)


def save_settings(settings: Settings, path: Path) -> None:
    """Write to a temp file in the same directory, fsync, then rename over the target."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(asdict(settings), fh, sort_keys=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def child_env(settings: Settings, port: int, base: Mapping[str, str] | None = None) -> dict:
    """Environment for a started tool: the parent env plus the shared settings and PORT."""
    env = dict(os.environ if base is None else base)
    env["PORT"] = str(port)
    env["DADSOPS_THEME"] = settings.theme
    if settings.aws_profile:
        env["AWS_PROFILE"] = settings.aws_profile
    if settings.aws_region:
        env["AWS_REGION"] = settings.aws_region
    return env
