import json
import os
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from dadsops import runner as runner_module
from dadsops.config import Settings
from dadsops.registry import load_registry
from dadsops.runner import Runner, port_open

SETTINGS = Settings(aws_profile="example", aws_region="us-east-1", theme="dark")


def marker_of(tool) -> Path:
    return Path(tool.start_command[3])


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def load_tool(write_registry, fake_tool_entry):
    def load(**kwargs):
        (tool,) = load_registry(write_registry([fake_tool_entry(**kwargs)]))
        return tool

    return load


@pytest.fixture
def make_runner(tmp_path: Path):
    """Runner whose 'port answers' check is the stub's marker file: no sockets involved."""
    runners: list[Runner] = []

    def make(probe=None, start_timeout: float = 5.0) -> Runner:
        def marker_probe(tool):
            return marker_of(tool).exists()

        runner = Runner(
            log_dir=tmp_path / "logs",
            probe=probe or marker_probe,
            start_timeout=start_timeout,
            poll_interval=0.05,
        )
        runners.append(runner)
        return runner

    yield make
    for runner in runners:
        runner.stop_all()


def test_start_runs_command_when_port_closed(make_runner, load_tool):
    tool = load_tool()
    runner = make_runner()

    result = runner.start(tool, SETTINGS)

    assert result.ok and result.started
    assert runner.owns(tool.name)
    recorded = json.loads(marker_of(tool).read_text())
    assert pid_alive(recorded["pid"])
    assert tool.name not in runner.errors


def test_started_tool_gets_shared_settings_in_env(make_runner, load_tool):
    tool = load_tool(port=9123)
    make_runner().start(tool, SETTINGS)

    env = json.loads(marker_of(tool).read_text())["env"]
    assert env["AWS_PROFILE"] == "example"
    assert env["AWS_REGION"] == "us-east-1"
    assert env["DADSOPS_THEME"] == "dark"
    assert env["PORT"] == "9123"


def test_port_placeholder_reaches_the_child_process(make_runner, write_registry, fake_tool_entry):
    entry = fake_tool_entry(port=9124)
    entry["start_command"] += ["--port", "{port}", "--bind=127.0.0.1:{port}"]
    (tool,) = load_registry(write_registry([entry]))

    assert make_runner().start(tool, SETTINGS).ok

    argv = json.loads(marker_of(tool).read_text())["argv"]
    assert argv[2:] == ["--port", "9124", "--bind=127.0.0.1:9124"]


def test_string_command_with_placeholder_reaches_the_child_process(
    make_runner, write_registry, fake_tool_entry
):
    entry = fake_tool_entry(port=9125)
    entry["start_command"] = shlex.join(entry["start_command"]) + " --port {port}"
    (tool,) = load_registry(write_registry([entry]))

    assert make_runner().start(tool, SETTINGS).ok

    argv = json.loads(marker_of(tool).read_text())["argv"]
    assert argv[2:] == ["--port", "9125"]


def test_no_second_process_when_port_already_open(make_runner, load_tool, monkeypatch):
    tool = load_tool()
    runner = make_runner(probe=lambda _tool: True)

    def no_spawn(*args, **kwargs):
        raise AssertionError("must not spawn when the port already answers")

    monkeypatch.setattr(runner_module.subprocess, "Popen", no_spawn)

    result = runner.start(tool, SETTINGS)

    assert result.ok and not result.started
    assert result.message == "already running"
    assert not runner.owns(tool.name)


def test_no_second_process_for_a_known_pid(make_runner, load_tool):
    tool = load_tool()
    runner = make_runner()
    runner.start(tool, SETTINGS)
    first_pid = json.loads(marker_of(tool).read_text())["pid"]
    marker_of(tool).unlink()

    result = runner.start(tool, SETTINGS)

    assert result.ok and not result.started
    assert not marker_of(tool).exists()
    assert pid_alive(first_pid)


def test_start_that_never_answers_times_out(make_runner, load_tool):
    tool = load_tool(port=9555)
    runner = make_runner(probe=lambda _tool: False, start_timeout=0.5)

    result = runner.start(tool, SETTINGS)

    assert not result.ok
    assert "did not answer on port 9555 within 0.5s" in result.message
    assert runner.errors[tool.name] == result.message
    assert not runner.owns(tool.name)
    assert wait_for(marker_of(tool).exists)
    pid = json.loads(marker_of(tool).read_text())["pid"]
    assert wait_for(lambda: not pid_alive(pid)), "timed-out process must be cleaned up"


def test_failing_command_reports_exit_code_and_output(make_runner, load_tool):
    tool = load_tool(mode="fail")
    runner = make_runner(probe=lambda _tool: False)

    result = runner.start(tool, SETTINGS)

    assert not result.ok
    assert "exited with code 3" in result.message
    assert "boom: stub refused to start" in result.message


def test_missing_executable_reports_failure(make_runner, write_registry, tool_dirs):
    entry = {"name": "tool_a", "path": str(tool_dirs["tool_a"]), "port": 9001}
    entry["start_command"] = "./no-such-binary"
    (tool,) = load_registry(write_registry([entry]))

    result = make_runner(probe=lambda _tool: False).start(tool, SETTINGS)

    assert not result.ok
    assert result.message.startswith("command failed")


def test_missing_tool_path_is_not_started(make_runner, write_registry, fake_tool_entry, tmp_path):
    entry = fake_tool_entry()
    entry["path"] = str(tmp_path / "nowhere")
    (tool,) = load_registry(write_registry([entry]))

    result = make_runner(probe=lambda _tool: False).start(tool, SETTINGS)

    assert not result.ok
    assert result.message.startswith("not found at")


def test_stop_kills_the_process_dadsops_started(make_runner, load_tool):
    tool = load_tool()
    runner = make_runner()
    runner.start(tool, SETTINGS)
    pid = json.loads(marker_of(tool).read_text())["pid"]

    assert runner.stop(tool) is True
    assert not runner.owns(tool.name)
    assert wait_for(lambda: not pid_alive(pid))


def test_stop_leaves_unrelated_process_alone(make_runner, load_tool, stub_script, tmp_path):
    tool = load_tool()
    unrelated = subprocess.Popen(
        [sys.executable, str(stub_script), "serve", str(tmp_path / "unrelated.marker")]
    )
    try:
        runner = make_runner(probe=lambda _tool: True)  # someone else holds the port

        assert runner.stop(tool) is False
        assert "not started by DadsOps" in runner.errors[tool.name]
        assert unrelated.poll() is None
    finally:
        unrelated.kill()
        unrelated.wait()


def test_port_open_on_an_ephemeral_listener():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        assert port_open(port)
    assert not port_open(port)
