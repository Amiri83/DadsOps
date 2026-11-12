"""The settings page form: Settings -> form values, and submitted values -> Settings.

The form is plain HTML (no JavaScript). Profiles are parallel `profile_name` /
`profile_region` rows plus one blank row to add a new one; `profile_remove`
holds the row indexes to drop and `default_profile` the default row's index.
Each app row is keyed by the app name (`port.<name>`, `profile.<name>`,
`enabled.<name>`, `args.<name>`); the hidden `app` fields list the rows shown,
so an unticked `enabled` checkbox reads as off.
"""

from __future__ import annotations

import shlex
from itertools import zip_longest

from dadsops.config import AppSettings, ConfigError, Profile, Settings
from dadsops.registry import Tool

Form = dict[str, list[str]]


def form_values(settings: Settings, tools: list[Tool]) -> dict:
    """What the settings page shows for the saved settings."""
    return {
        "theme": settings.theme,
        "log_dir": settings.log_dir,
        "profiles": [
            {"name": p.name, "region": p.region, "default": p.name == settings.default_profile}
            for p in settings.profiles
        ],
        "apps": [_app_row(tool, settings.app(tool.name)) for tool in tools],
    }


def _app_row(tool: Tool, app: AppSettings) -> dict:
    return {
        "name": tool.name,
        "registry_port": tool.port,
        "port": "" if app.port is None else str(app.port),
        "profile": app.profile or "",
        "enabled": app.enabled,
        "extra_args": shlex.join(app.extra_args),
    }


def read_form(form: Form, tools: list[Tool]) -> dict:
    """The submitted values, shaped like form_values() so a rejected form re-renders as typed."""

    def first(key: str, default: str = "") -> str:
        return form.get(key, [default])[0]

    removed = set(form.get("profile_remove", []))
    default_row = first("default_profile")
    profiles = []
    rows = zip_longest(form.get("profile_name", []), form.get("profile_region", []), fillvalue="")
    for index, (name, region) in enumerate(rows):
        name, region = name.strip(), region.strip()
        if str(index) in removed or not (name or region):
            continue
        profiles.append({"name": name, "region": region, "default": str(index) == default_row})

    registry_ports = {tool.name: tool.port for tool in tools}
    apps = [
        {
            "name": name,
            "registry_port": registry_ports.get(name),
            "port": first(f"port.{name}").strip(),
            "profile": first(f"profile.{name}"),
            "enabled": f"enabled.{name}" in form,
            "extra_args": first(f"args.{name}").strip(),
        }
        for name in form.get("app", [])
    ]
    return {
        "theme": first("theme", "light"),
        "log_dir": first("log_dir").strip(),
        "profiles": profiles,
        "apps": apps,
    }


def settings_from_form(values: dict, current: Settings) -> Settings:
    """Validated Settings from read_form() values; apps not on the form keep their settings."""
    profiles = tuple(Profile(name=p["name"], region=p["region"]) for p in values["profiles"])
    defaults = [p["name"] for p in values["profiles"] if p["default"]]
    if len(profiles) == 1 and not defaults:
        defaults = [profiles[0].name]
    if len(defaults) > 1:
        raise ConfigError("exactly one AWS profile must be marked as the default")

    apps = dict(current.apps)
    for row in values["apps"]:
        apps[row["name"]] = AppSettings(
            port=_port(row),
            profile=row["profile"] or None,
            enabled=row["enabled"],
            extra_args=_extra_args(row),
        )
    return Settings(
        profiles=profiles,
        default_profile=defaults[0] if defaults else None,
        log_dir=values["log_dir"],
        theme=values["theme"],
        apps=apps,
    )


def _port(row: dict) -> int | None:
    if not row["port"]:
        return None
    try:
        return int(row["port"])
    except ValueError:
        message = f"{row['name']}: port must be a whole number, got {row['port']!r}"
        raise ConfigError(message) from None


def _extra_args(row: dict) -> tuple[str, ...]:
    try:
        return tuple(shlex.split(row["extra_args"]))
    except ValueError as exc:
        raise ConfigError(f"{row['name']}: extra args cannot be parsed: {exc}") from None
