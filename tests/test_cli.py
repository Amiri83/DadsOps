import subprocess
import sys
from pathlib import Path

import pytest

from dadsops import cli


class Execed(Exception):
    """Raised by the fake os.execv so the test sees what would have been executed."""


@pytest.fixture
def served(monkeypatch) -> list[tuple]:
    calls: list[tuple] = []
    monkeypatch.setattr(cli, "checkout_root", lambda: None)
    monkeypatch.setattr(
        cli, "serve", lambda host, port, registry: calls.append((host, port, registry))
    )
    return calls


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch) -> dict:
    """A fake checkout with no venv yet; records venv creation, subprocesses and the exec."""
    seen: dict = {"created": [], "runs": [], "importable": False}
    monkeypatch.setattr(cli, "checkout_root", lambda: tmp_path)

    def fake_create(path: Path, with_pip: bool) -> None:
        seen["created"].append(path)
        (path / "bin").mkdir(parents=True)
        (path / "bin" / "python").touch()

    def fake_run(command, **kwargs):
        seen["runs"].append((command, kwargs))
        probe = "-c" in command
        return subprocess.CompletedProcess(command, 1 if probe and not seen["importable"] else 0)

    def fake_execv(path, argv):
        raise Execed(argv)

    monkeypatch.setattr(cli.venv, "create", fake_create)
    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(cli.os, "execv", fake_execv)
    monkeypatch.setattr(cli, "serve", lambda *args: pytest.fail("must re-exec in the venv first"))
    monkeypatch.setenv("PYTHONPATH", "/somewhere/else")
    monkeypatch.delenv("DADSOPS_PORT", raising=False)
    seen["root"] = tmp_path
    return seen


def test_bare_run_from_a_checkout_creates_the_venv_installs_and_reexecs(checkout):
    root = checkout["root"]
    python = str(root / ".venv" / "bin" / "python")

    with pytest.raises(Execed) as execed:
        cli.main([])

    assert checkout["created"] == [root / ".venv"]
    commands = [command for command, _ in checkout["runs"]]
    assert [python, "-m", "pip", "install", "-e", str(root)] in commands
    probes = [kwargs for command, kwargs in checkout["runs"] if "-c" in command]
    assert probes and all("PYTHONPATH" not in kwargs["env"] for kwargs in probes)
    assert execed.value.args[0] == [
        python,
        "-m",
        "dadsops",
        "run",
        "--host",
        "127.0.0.1",
        "--port",
        "8080",
    ]


def test_existing_complete_venv_is_reused_without_pip(checkout):
    checkout["importable"] = True
    (checkout["root"] / ".venv" / "bin").mkdir(parents=True)
    (checkout["root"] / ".venv" / "bin" / "python").touch()

    with pytest.raises(Execed):
        cli.main(["run", "--registry", "custom.yaml"])

    assert checkout["created"] == []
    assert not any("pip" in command for command, _ in checkout["runs"])


PYPROJECT_WITH_TOOLS = """
[project]
name = "dadsops"
dependencies = ["fastapi>=0.110", "uvicorn[standard]>=0.29", "tool-a>=1.2", "tool_b >= 1.6"]
[project.optional-dependencies]
tools = ["tool-c>=0.1"]
"""


def test_required_distributions_come_from_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text(PYPROJECT_WITH_TOOLS)

    assert cli.required_distributions(tmp_path) == ["fastapi", "uvicorn", "tool-a", "tool_b"]
    assert cli.required_distributions(tmp_path / "missing") == []


def test_the_repo_bootstrap_installs_the_bundled_tools_not_the_held_one():
    names = cli.required_distributions(Path(__file__).resolve().parents[1])

    assert {"ec2patcher", "aws-iplens"} <= set(names)
    assert "aws-tflens" not in names


def test_venv_missing_a_bundled_tool_is_reinstalled(checkout, monkeypatch):
    """Modules import fine but a bundled tool's distribution is missing: pip runs."""
    (checkout["root"] / "pyproject.toml").write_text(PYPROJECT_WITH_TOOLS)
    (checkout["root"] / ".venv" / "bin").mkdir(parents=True)
    (checkout["root"] / ".venv" / "bin" / "python").touch()

    def dist_probe_fails(command, **kwargs):
        checkout["runs"].append((command, kwargs))
        failed = "-c" in command and "importlib.metadata" in command[2]
        return subprocess.CompletedProcess(command, 1 if failed else 0)

    monkeypatch.setattr(cli.subprocess, "run", dist_probe_fails)
    with pytest.raises(Execed):
        cli.main([])

    commands = [command for command, _ in checkout["runs"]]
    dist_probe = next(c for c in commands if "-c" in c and "importlib.metadata" in c[2])
    assert dist_probe[3:] == ["fastapi", "uvicorn", "tool-a", "tool_b"]
    assert any(c[1:4] == ["-m", "pip", "install"] for c in commands)


def test_registry_flag_is_passed_through_the_reexec(checkout):
    with pytest.raises(Execed) as execed:
        cli.main(["run", "--registry", "custom.yaml"])

    assert execed.value.args[0][-2:] == ["--registry", "custom.yaml"]


def test_inside_the_checkout_venv_it_serves(checkout, monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "prefix", str(checkout["root"] / ".venv"))
    monkeypatch.setattr(cli, "serve", lambda *args: calls.append(args))

    assert cli.main([]) == 0
    assert calls == [("127.0.0.1", 8080, None)]
    assert checkout["runs"] == []


def test_installed_serves_directly_with_default_registry(served, monkeypatch):
    monkeypatch.delenv("DADSOPS_PORT", raising=False)
    assert cli.main([]) == 0
    assert served == [("127.0.0.1", 8080, None)]


def test_port_from_environment_and_flag(served, monkeypatch):
    monkeypatch.setenv("DADSOPS_PORT", "9100")
    cli.main(["run"])
    cli.main(["run", "--port", "9200"])
    assert [(host, port) for host, port, _ in served] == [("127.0.0.1", 9100), ("127.0.0.1", 9200)]


@pytest.mark.parametrize("value", ["abc", "0", "70000"])
def test_bad_port_is_rejected(served, value):
    with pytest.raises(SystemExit):
        cli.main(["run", "--port", value])
    assert served == []


def test_install_outside_a_checkout_does_nothing(served, capsys):
    assert cli.main(["install"]) == 0
    assert "not a source checkout" in capsys.readouterr().out
