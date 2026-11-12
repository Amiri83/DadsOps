"""Start, stop and check the tool processes.

A tool counts as running when its port answers (or its health path responds) or
when DadsOps holds a live process for it. Stop only ever signals processes this
runner spawned itself, never whatever else happens to hold the port.
"""

from __future__ import annotations

import http.client
import os
import signal
import socket
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from dadsops.config import ConfigError, Launch
from dadsops.registry import Tool

HOST = "127.0.0.1"


def port_open(port: int, host: str = HOST, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def is_ready(tool: Tool, timeout: float = 0.5) -> bool:
    """TCP check, or an HTTP GET of health_path (any non-5xx answer) when one is set."""
    if tool.health_path is None:
        return port_open(tool.port, timeout=timeout)
    conn = http.client.HTTPConnection(HOST, tool.port, timeout=timeout)
    try:
        conn.request("GET", tool.health_path)
        return conn.getresponse().status < 500
    except (OSError, http.client.HTTPException):
        return False
    finally:
        conn.close()


@dataclass(frozen=True)
class StartResult:
    ok: bool
    started: bool
    message: str


class Runner:
    def __init__(
        self,
        probe: Callable[[Tool], bool] = is_ready,
        start_timeout: float = 30.0,
        poll_interval: float = 0.2,
    ) -> None:
        self.start_timeout = start_timeout
        self.poll_interval = poll_interval
        self.errors: dict[str, str] = {}
        self._probe = probe
        self._procs: dict[str, subprocess.Popen] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def owns(self, name: str) -> bool:
        proc = self._procs.get(name)
        return proc is not None and proc.poll() is None

    def is_running(self, tool: Tool) -> bool:
        return self.owns(tool.name) or self._probe(tool)

    def start(self, launch: Launch) -> StartResult:
        name = launch.tool.name
        with self._lock(name):
            result = self._start(launch)
        if result.ok:
            self.errors.pop(name, None)
        else:
            self.errors[name] = result.message
        return result

    def stop(self, tool: Tool) -> bool:
        with self._lock(tool.name):
            if not self.owns(tool.name):
                self._procs.pop(tool.name, None)
                self.errors[tool.name] = "not started by DadsOps, left running"
                return False
            self._terminate(tool.name)
        self.errors.pop(tool.name, None)
        return True

    def stop_all(self) -> None:
        for name in list(self._procs):
            with self._lock(name):
                if name in self._procs:
                    self._terminate(name)

    def _lock(self, name: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(name, threading.Lock())

    def _start(self, launch: Launch) -> StartResult:
        tool = launch.tool
        if self.is_running(tool):
            return StartResult(ok=True, started=False, message="already running")
        if tool.coming_soon:
            return StartResult(ok=False, started=False, message="coming soon")
        if not launch.enabled:
            return StartResult(ok=False, started=False, message="disabled in settings")
        if not tool.installed:
            return StartResult(ok=False, started=False, message=f"not found at {tool.path}")
        try:
            command = launch.command()
        except ConfigError as exc:
            return StartResult(ok=False, started=False, message=str(exc))

        log_path = launch.log_path
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            log = log_path.open("wb")
        except OSError as exc:
            reason = f"cannot write log {log_path}: {exc.strerror or exc}"
            return StartResult(ok=False, started=False, message=reason)
        with log:
            try:
                proc = subprocess.Popen(
                    command,
                    cwd=tool.path,
                    env=launch.env(),
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            except OSError as exc:
                reason = f"command failed: {exc.strerror or exc}"
                return StartResult(ok=False, started=False, message=reason)
        self._procs[tool.name] = proc

        deadline = time.monotonic() + self.start_timeout
        while time.monotonic() < deadline:
            if self._probe(tool):
                return StartResult(ok=True, started=True, message=f"started (pid {proc.pid})")
            code = proc.poll()
            if code is not None:
                del self._procs[tool.name]
                reason = f"command exited with code {code}{_last_line(log_path)}"
                return StartResult(ok=False, started=False, message=reason)
            time.sleep(self.poll_interval)

        self._terminate(tool.name)
        reason = f"did not answer on port {tool.port} within {self.start_timeout:g}s"
        return StartResult(ok=False, started=False, message=reason)

    def _terminate(self, name: str, grace: float = 5.0) -> None:
        proc = self._procs.pop(name)
        if proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(grace)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        except ProcessLookupError:
            proc.wait()


def _last_line(log_path: Path) -> str:
    lines = log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    return f": {lines[-1][:200]}" if lines else ""
