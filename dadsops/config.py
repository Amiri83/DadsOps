"""Shared settings for every app, and how they combine with the registry.

Only names, regions, ports, paths and plain arguments are stored: never
credentials. Unknown keys are rejected and extra args that look like a secret
are refused, so the file cannot quietly grow into a secrets store.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from dadsops.logs import log_file
from dadsops.paths import DEFAULT_LOG_DIR
from dadsops.registry import NAME_RE, Tool

THEMES = ("light", "dark")
PROFILE_RE = re.compile(r"^[A-Za-z0-9_.@+=,-]{1,64}$")
REGION_RE = re.compile(r"^[a-z]{2}(-[a-z]+)+-[0-9]$")
SECRET_RE = re.compile(r"secret|token|passw|access[_-]?key|\b(AKIA|ASIA)[A-Z0-9]{16}\b", re.I)
TOP_KEYS = {"theme", "log_dir", "profiles", "apps"}
PROFILE_KEYS = {"name", "region", "default"}
APP_KEYS = {"port", "profile", "enabled", "extra_args"}


class ConfigError(ValueError):
    """The settings file or a submitted value is invalid."""


@dataclass(frozen=True)
class Profile:
    name: str
    region: str


@dataclass(frozen=True)
class AppSettings:
    """Per-app overrides; None / empty means "use the registry or the global default"."""

    port: int | None = None
    profile: str | None = None
    enabled: bool = True
    extra_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class Settings:
    profiles: tuple[Profile, ...] = ()
    default_profile: str | None = None
    log_dir: str = DEFAULT_LOG_DIR
    theme: str = "light"
    apps: Mapping[str, AppSettings] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate(self)

    def app(self, name: str) -> AppSettings:
        return self.apps.get(name, AppSettings())

    def profile(self, name: str | None) -> Profile | None:
        """The named profile, or the default one when name is None."""
        wanted = name or self.default_profile
        return next((p for p in self.profiles if p.name == wanted), None)


def _validate(settings: Settings) -> None:
    if settings.theme not in THEMES:
        raise ConfigError(f"theme must be one of: {', '.join(THEMES)}")
    if not isinstance(settings.log_dir, str) or not settings.log_dir.strip():
        raise ConfigError("log directory must be a non-empty path")
    if "\0" in settings.log_dir:
        raise ConfigError("log directory must not contain a NUL byte")

    names = [p.name for p in settings.profiles]
    for profile in settings.profiles:
        if not isinstance(profile.name, str) or not PROFILE_RE.match(profile.name):
            raise ConfigError(
                f"AWS profile {profile.name!r}: use letters, digits and _.@+=,- (1-64 characters)"
            )
        if not isinstance(profile.region, str) or not REGION_RE.match(profile.region):
            raise ConfigError(
                f"AWS profile {profile.name}: region must look like us-east-1, "
                f"got {profile.region!r}"
            )
    if len(set(names)) != len(names):
        raise ConfigError("AWS profile names must be unique")
    if names and settings.default_profile not in names:
        raise ConfigError("exactly one AWS profile must be marked as the default")
    if not names and settings.default_profile is not None:
        raise ConfigError("the default AWS profile must be one of the listed profiles")

    ports: dict[int, str] = {}
    for name, app in settings.apps.items():
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise ConfigError(f"app name {name!r} must use only letters, digits, '-' or '_'")
        _validate_app(name, app, names)
        if app.port is not None:
            if app.port in ports:
                raise ConfigError(f"{name}: port {app.port} is already used by {ports[app.port]}")
            ports[app.port] = name


def _validate_app(name: str, app: AppSettings, profile_names: list[str]) -> None:
    port = app.port
    if port is not None and (isinstance(port, bool) or not isinstance(port, int)):
        raise ConfigError(f"{name}: port must be a whole number, got {port!r}")
    if port is not None and not 1 <= port <= 65535:
        raise ConfigError(f"{name}: port must be from 1 to 65535, got {port}")
    if app.profile is not None and app.profile not in profile_names:
        raise ConfigError(f"{name}: AWS profile {app.profile!r} is not in the profile list")
    if not isinstance(app.enabled, bool):
        raise ConfigError(f"{name}: enabled must be true or false")
    if not isinstance(app.extra_args, tuple) or not all(
        isinstance(arg, str) for arg in app.extra_args
    ):
        raise ConfigError(f"{name}: extra args must be a list of strings")
    for arg in app.extra_args:
        if not arg or "\0" in arg:
            raise ConfigError(f"{name}: extra args must be non-empty strings")
        if SECRET_RE.search(arg):
            raise ConfigError(
                f"{name}: extra arg {arg!r} looks like a secret; DadsOps never stores secrets"
            )


def load_settings(path: Path) -> Settings:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Settings()
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc.strerror or exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    try:
        return settings_from_dict(data)
    except ConfigError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def settings_from_dict(data: Any) -> Settings:
    if data is None:
        return Settings()
    _require_mapping(data, "settings", TOP_KEYS)

    raw_profiles = data.get("profiles", [])
    if not isinstance(raw_profiles, list):
        raise ConfigError("profiles must be a list")
    profiles, defaults = [], []
    for index, entry in enumerate(raw_profiles, 1):
        _require_mapping(entry, f"profile #{index}", PROFILE_KEYS)
        is_default = entry.get("default", False)
        if not isinstance(is_default, bool):
            raise ConfigError(f"profile #{index}: default must be true or false")
        profiles.append(Profile(name=entry.get("name"), region=entry.get("region")))
        if is_default:
            defaults.append(entry.get("name"))
    if len(defaults) > 1:
        raise ConfigError("exactly one AWS profile must be marked as the default")

    raw_apps = data.get("apps") or {}
    if not isinstance(raw_apps, dict):
        raise ConfigError("apps must be a mapping of app name to settings")
    apps = {name: _app_from_dict(name, value) for name, value in raw_apps.items()}

    return Settings(
        profiles=tuple(profiles),
        default_profile=defaults[0] if defaults else None,
        log_dir=data.get("log_dir", DEFAULT_LOG_DIR),
        theme=data.get("theme", "light"),
        apps=apps,
    )


def _app_from_dict(name: Any, data: Any) -> AppSettings:
    _require_mapping(data, f"app {name}", APP_KEYS)
    extra_args = data.get("extra_args", [])
    if not isinstance(extra_args, list):
        raise ConfigError(f"{name}: extra args must be a list of strings")
    return AppSettings(
        port=data.get("port"),
        profile=data.get("profile"),
        enabled=data.get("enabled", True),
        extra_args=tuple(extra_args),
    )


def _require_mapping(data: Any, where: str, allowed: set[str]) -> None:
    if not isinstance(data, dict):
        raise ConfigError(f"{where} must be a mapping")
    unknown = sorted(map(str, set(data) - allowed))
    if unknown:
        raise ConfigError(f"{where}: unknown setting(s): {', '.join(unknown)}")


def settings_to_dict(settings: Settings) -> dict:
    """The file form: per-app entries list only what differs from the default."""
    apps = {}
    for name, app in settings.apps.items():
        entry: dict[str, Any] = {}
        if app.port is not None:
            entry["port"] = app.port
        if app.profile is not None:
            entry["profile"] = app.profile
        if not app.enabled:
            entry["enabled"] = False
        if app.extra_args:
            entry["extra_args"] = list(app.extra_args)
        if entry:
            apps[name] = entry
    profiles = []
    for profile in settings.profiles:
        entry = {"name": profile.name, "region": profile.region}
        if profile.name == settings.default_profile:
            entry["default"] = True
        profiles.append(entry)
    return {
        "theme": settings.theme,
        "log_dir": settings.log_dir,
        "profiles": profiles,
        "apps": apps,
    }


def save_settings(settings: Settings, path: Path) -> None:
    """Write to a temp file in the same directory, fsync, then rename over the target."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(settings_to_dict(settings), fh, sort_keys=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


@dataclass(frozen=True)
class Launch:
    """One tool as it will be started: registry entry plus the settings that apply to it."""

    tool: Tool
    enabled: bool
    profile: Profile | None
    log_dir: Path
    theme: str
    extra_args: tuple[str, ...]

    @property
    def log_path(self) -> Path:
        return log_file(self.log_dir, self.tool.name)

    def command(self) -> tuple[str, ...]:
        values = {"port": str(self.tool.port), "log_dir": str(self.log_dir)}
        if self.profile is not None:
            values.update(profile=self.profile.name, region=self.profile.region)
        missing = sorted(self.tool.placeholders() - values.keys())
        if missing:
            used = ", ".join(f"{{{name}}}" for name in missing)
            raise ConfigError(f"start_command uses {used} but no AWS profile is configured")
        return self.tool.command(values) + self.extra_args

    def env(self, base: Mapping[str, str] | None = None) -> dict[str, str]:
        """The parent environment plus PORT, the log dir, the theme and the AWS profile."""
        env = dict(os.environ if base is None else base)
        env["PORT"] = str(self.tool.port)
        env["DADSOPS_LOG_DIR"] = str(self.log_dir)
        env["DADSOPS_THEME"] = self.theme
        if self.profile is not None:
            env["AWS_PROFILE"] = self.profile.name
            env["AWS_REGION"] = self.profile.region
        return env


def resolve(tools: list[Tool], settings: Settings) -> list[Launch]:
    """Apply per-app settings over the registry; effective ports must stay unique."""
    log_dir = Path(settings.log_dir).expanduser()
    launches, owners = [], {}
    for tool in tools:
        app = settings.app(tool.name)
        effective = replace(tool, port=app.port or tool.port)
        if effective.port in owners:
            raise ConfigError(
                f"{tool.name}: port {effective.port} is already used by {owners[effective.port]}"
            )
        owners[effective.port] = tool.name
        launches.append(
            Launch(
                tool=effective,
                enabled=app.enabled,
                profile=settings.profile(app.profile),
                log_dir=log_dir,
                theme=settings.theme,
                extra_args=app.extra_args,
            )
        )
    return launches
