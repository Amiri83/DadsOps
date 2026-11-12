"""Load and validate the tool registry (tools.yaml)."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REQUIRED_KEYS = ("name", "path", "start_command", "port")
PORT_PLACEHOLDER = "{port}"
OPTIONAL_KEYS = ("description", "health_path", "coming_soon")
NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class RegistryError(ValueError):
    """The registry file is unreadable or invalid."""


@dataclass(frozen=True)
class Tool:
    name: str
    path: Path
    start_command: tuple[str, ...]
    port: int
    description: str = ""
    health_path: str | None = None
    coming_soon: bool = False

    def command(self) -> tuple[str, ...]:
        """start_command as run: every literal {port} replaced by the tool's port."""
        port = str(self.port)
        return tuple(part.replace(PORT_PLACEHOLDER, port) for part in self.start_command)

    @property
    def installed(self) -> bool:
        return self.path.is_dir()

    @property
    def status(self) -> str:
        """Live status badge: checked against the filesystem on every call."""
        if self.coming_soon:
            return "coming soon"
        return "installed" if self.installed else "not found"


def load_registry(path: Path) -> list[Tool]:
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RegistryError(f"cannot read {path}: {exc.strerror or exc}") from exc
    except yaml.YAMLError as exc:
        raise RegistryError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("tools"), list):
        raise RegistryError(f"{path} must contain a top-level 'tools:' list")

    base = path.resolve().parent
    tools = [_parse_entry(entry, i, base) for i, entry in enumerate(data["tools"], 1)]
    _reject_duplicates([t.name for t in tools], "name")
    _reject_duplicates([t.port for t in tools], "port")
    return tools


def _parse_entry(entry: Any, index: int, base: Path) -> Tool:
    where = f"tool #{index}"
    if not isinstance(entry, dict):
        raise RegistryError(f"{where} must be a mapping of keys to values")
    if isinstance(entry.get("name"), str):
        where = f"tool #{index} ({entry['name']})"

    missing = [key for key in REQUIRED_KEYS if key not in entry]
    if missing:
        raise RegistryError(f"{where}: missing required key(s): {', '.join(missing)}")
    unknown = sorted(set(entry) - set(REQUIRED_KEYS) - set(OPTIONAL_KEYS))
    if unknown:
        raise RegistryError(f"{where}: unknown key(s): {', '.join(unknown)}")

    name = entry["name"]
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise RegistryError(f"{where}: name must use only letters, digits, '-' or '_'")

    raw_path = entry["path"]
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise RegistryError(f"{where}: path must be a non-empty string")

    port = entry["port"]
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise RegistryError(f"{where}: port must be an integer from 1 to 65535, got {port!r}")

    health_path = entry.get("health_path")
    if health_path is not None and (
        not isinstance(health_path, str) or not health_path.startswith("/")
    ):
        raise RegistryError(f"{where}: health_path must start with '/'")

    coming_soon = entry.get("coming_soon", False)
    if not isinstance(coming_soon, bool):
        raise RegistryError(f"{where}: coming_soon must be true or false")

    description = entry.get("description", "")
    if not isinstance(description, str):
        raise RegistryError(f"{where}: description must be a string")

    return Tool(
        name=name,
        path=(base / Path(raw_path).expanduser()).resolve(),
        start_command=_parse_command(entry["start_command"], where),
        port=port,
        description=description.strip(),
        health_path=health_path,
        coming_soon=coming_soon,
    )


def _parse_command(value: Any, where: str) -> tuple[str, ...]:
    if isinstance(value, str):
        try:
            parts = shlex.split(value)
        except ValueError as exc:
            raise RegistryError(f"{where}: start_command cannot be parsed: {exc}") from exc
    elif isinstance(value, list) and all(isinstance(part, str) for part in value):
        parts = value
    else:
        raise RegistryError(f"{where}: start_command must be a string or a list of strings")
    if not parts or not all(part.strip() for part in parts):
        raise RegistryError(f"{where}: start_command must not be empty")
    return tuple(parts)


def _reject_duplicates(values: list[Any], key: str) -> None:
    seen: set[Any] = set()
    for value in values:
        if value in seen:
            raise RegistryError(f"duplicate {key}: {value!r}")
        seen.add(value)
