"""The `dadsops` command.

Only the standard library is imported at module level so a bare `python3 -m
dadsops` from a checkout can bootstrap the venv before FastAPI is installed.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tomllib
import venv
from pathlib import Path

from dadsops.paths import checkout_root, config_path, registry_path

REQUIRED_MODULES = ("dadsops", "fastapi", "uvicorn", "yaml", "jinja2")
LOOPBACK = ("127.0.0.1", "localhost", "::1")
REQUIREMENT_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
DIST_PROBE = "import importlib.metadata as m, sys; [m.distribution(n) for n in sys.argv[1:]]"


def port_number(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a port number: {value!r}") from None
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"port out of range: {port}")
    return port


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dadsops", description="Daddy's AWS Toolbox launcher")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("install", help="create or reuse .venv in a checkout and install into it")
    run = commands.add_parser("run", help="install if needed, then serve the toolbox")
    run.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    run.add_argument(
        "--port",
        type=port_number,
        default=os.environ.get("DADSOPS_PORT", "8080"),
        help="port to serve on (default $DADSOPS_PORT or 8080)",
    )
    run.add_argument(
        "--registry",
        type=Path,
        help="tool registry file (default $DADSOPS_REGISTRY, else tools.yaml in a checkout, "
        "else ~/.config/dadsops/tools.yaml)",
    )
    return parser


def can_import(python: Path, venv_dir: Path, *modules: str) -> bool:
    """Whether the venv itself provides the modules (ignoring PYTHONPATH and the cwd)."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    probe = [str(python), "-c", "import " + ", ".join(modules)]
    return subprocess.run(probe, cwd=venv_dir, env=env, capture_output=True).returncode == 0


def required_distributions(root: Path) -> list[str]:
    """The distribution names in pyproject.toml's [project].dependencies (the bundled tools
    included), so the checkout venv gets exactly what `pip install dadsops` gets."""
    try:
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    names = []
    for requirement in project.get("project", {}).get("dependencies", []):
        match = REQUIREMENT_NAME_RE.match(requirement)
        if match:
            names.append(match.group(1))
    return names


def has_distributions(python: Path, venv_dir: Path, names: list[str]) -> bool:
    """Whether every named distribution is installed in the venv."""
    if not names:
        return True
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    probe = [str(python), "-c", DIST_PROBE, *names]
    return subprocess.run(probe, cwd=venv_dir, env=env, capture_output=True).returncode == 0


def install(root: Path) -> Path:
    """Create <root>/.venv if missing and install the project into it if any import fails
    or any dependency (a bundled tool, say) is missing."""
    venv_dir = root / ".venv"
    python = venv_dir / "bin" / "python"
    if not python.exists():
        print(f"dadsops: creating venv in {venv_dir}")
        venv.create(venv_dir, with_pip=True)
    complete = can_import(python, venv_dir, *REQUIRED_MODULES) and has_distributions(
        python, venv_dir, required_distributions(root)
    )
    if not complete:
        print("dadsops: installing dadsops, its dependencies and the bundled tools into the venv")
        command = [str(python), "-m", "pip", "install", "-e", str(root)]
        if can_import(python, venv_dir, "setuptools"):
            command.append("--no-build-isolation")  # reuse it, so this also works offline
        subprocess.run(command, check=True)
    return python


def serve(host: str, port: int, registry: Path | None) -> None:
    import uvicorn

    from dadsops.app import create_app

    registry = registry_path(registry)
    if host not in LOOPBACK:
        print(f"dadsops: warning, binding to {host} exposes the launcher beyond this machine")
    print(f"dadsops: serving on http://{host}:{port}/ (registry {registry})")
    uvicorn.run(create_app(registry, config_path()), host=host, port=port)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(argv or ["run"])
    root = checkout_root()

    if args.command == "install":
        if root is None:
            print("dadsops: not a source checkout, nothing to install")
            return 0
        install(root)
        return 0

    if root is not None and Path(sys.prefix).resolve() != (root / ".venv").resolve():
        python = install(root)
        command = [str(python), "-m", "dadsops", "run", "--host", args.host]
        command += ["--port", str(args.port)]
        if args.registry is not None:
            command += ["--registry", str(args.registry)]
        os.execv(command[0], command)
    serve(args.host, args.port, args.registry)
    return 0
