import sys
import textwrap
from pathlib import Path

import pytest
import yaml

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
