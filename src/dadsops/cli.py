"""The `dadsops` command.

Only the standard library is imported at module level so `python -m dadsops`
can bootstrap the venv from a bare interpreter before FastAPI is installed.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import venv
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VENV_DIR = REPO_ROOT / ".venv"
REQUIRED_MODULES = ("dadsops", "fastapi", "uvicorn", "yaml", "jinja2")
LOOPBACK = ("127.0.0.1", "localhost", "::1")


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
    commands.add_parser("install", help="create or reuse the venv and install missing deps")
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
        default=os.environ.get("DADSOPS_REGISTRY", str(REPO_ROOT / "tools.yaml")),
        help="tool registry file (default $DADSOPS_REGISTRY or tools.yaml in the repo)",
    )
    return parser


def is_source_checkout() -> bool:
    return (REPO_ROOT / "pyproject.toml").is_file()


def can_import(python: Path, *modules: str) -> bool:
    """Whether the venv itself provides the modules (ignoring PYTHONPATH and the cwd)."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    probe = [str(python), "-c", "import " + ", ".join(modules)]
    return subprocess.run(probe, cwd=VENV_DIR, env=env, capture_output=True).returncode == 0


def install() -> Path:
    """Create .venv if missing and pip-install this project into it if any import fails."""
    python = VENV_DIR / "bin" / "python"
    if not python.exists():
        print(f"dadsops: creating venv in {VENV_DIR}")
        venv.create(VENV_DIR, with_pip=True)
    if not can_import(python, *REQUIRED_MODULES):
        print("dadsops: installing dependencies")
        command = [str(python), "-m", "pip", "install", "-e", str(REPO_ROOT)]
        if can_import(python, "setuptools"):
            command.append("--no-build-isolation")  # reuse it, so this also works offline
        subprocess.run(command, check=True)
    return python


def serve(host: str, port: int, registry: Path) -> None:
    import uvicorn

    from dadsops.app import create_app
    from dadsops.config import config_path

    if host not in LOOPBACK:
        print(f"dadsops: warning, binding to {host} exposes the launcher beyond this machine")
    print(f"dadsops: serving on http://{host}:{port}/ (registry {registry})")
    uvicorn.run(create_app(registry, config_path()), host=host, port=port)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(argv or ["run"])

    if args.command == "install":
        if not is_source_checkout():
            print("dadsops: not a source checkout, nothing to install")
            return 0
        install()
        return 0

    if is_source_checkout():
        python = install()
        if Path(sys.prefix).resolve() != VENV_DIR.resolve():
            command = [str(python), "-m", "dadsops", "run"]
            command += ["--host", args.host, "--port", str(args.port)]
            command += ["--registry", str(args.registry)]
            os.execv(command[0], command)
    serve(args.host, args.port, args.registry)
    return 0
