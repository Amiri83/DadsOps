import sys
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from dadsops import registry as registry_module

STUB_SOURCE = textwrap.dedent(
    """
    import json, os, sys, time
    mode, marker = sys.argv[1], sys.argv[2]
    with open(marker + ".tmp", "w") as fh:
        json.dump({"pid": os.getpid(), "argv": sys.argv[1:], "env": dict(os.environ)}, fh)
    os.replace(marker + ".tmp", marker)
    if mode == "fail":
        print("boom: stub refused to start", flush=True)
        sys.exit(3)
    time.sleep(60)
    """
)


@pytest.fixture
def lookup_dirs(tmp_path: Path, monkeypatch) -> dict[str, Path]:
    """An isolated PATH, DadsOps venv bin/ and home: a tool without a path is only found
    where the test puts its executable."""
    dirs = {name: tmp_path / name for name in ("path_bin", "venv_bin", "home")}
    for path in dirs.values():
        path.mkdir()
    monkeypatch.setenv("PATH", str(dirs["path_bin"]))
    monkeypatch.setenv("HOME", str(dirs["home"]))
    monkeypatch.setattr(registry_module, "VENV_BIN", dirs["venv_bin"])
    return dirs


@pytest.fixture
def fake_tool_server():
    """Start a tiny HTTP server on 127.0.0.1, port 0 (the OS picks a free one) that answers
    every GET with the given headers; returns its port. Stopped after the test."""
    servers: list[ThreadingHTTPServer] = []

    def start(headers: list[tuple[str, str]] | None = None, redirect_to: str | None = None):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if redirect_to and self.path == "/":
                    self.send_response(302)
                    self.send_header("Location", redirect_to)
                else:
                    self.send_response(200)
                    for name, value in headers or []:
                        self.send_header(name, value)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *args) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server.server_address[1]

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.fixture
def make_executable():
    def make(directory: Path, name: str, body: str = "#!/bin/sh\nexit 0\n") -> Path:
        path = directory / name
        path.write_text(body)
        path.chmod(0o755)
        return path

    return make


@pytest.fixture
def stub_script(tmp_path: Path) -> Path:
    """A fake tool: records its pid, args + env to a marker file, then sleeps (or fails)."""
    script = tmp_path / "stub_tool.py"
    script.write_text(STUB_SOURCE)
    return script


@pytest.fixture
def write_registry(tmp_path: Path):
    def write(entries: list[dict], name: str = "tools.yaml") -> Path:
        path = tmp_path / name
        path.write_text(yaml.safe_dump({"tools": entries}, sort_keys=False))
        return path

    return write


@pytest.fixture
def tool_dirs(tmp_path: Path) -> dict[str, Path]:
    dirs = {name: tmp_path / name for name in ("tool_a", "tool_b")}
    for path in dirs.values():
        path.mkdir()
    return dirs


@pytest.fixture
def fake_tool_entry(tool_dirs, stub_script, tmp_path):
    def entry(name: str = "tool_a", mode: str = "serve", port: int = 9001) -> dict:
        marker = tmp_path / f"{name}.marker"
        return {
            "name": name,
            "path": str(tool_dirs.get(name, tool_dirs["tool_a"])),
            "start_command": [sys.executable, str(stub_script), mode, str(marker)],
            "port": port,
        }

    return entry
