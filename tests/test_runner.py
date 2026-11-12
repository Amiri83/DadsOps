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
from dadsops.config import AppSettings, Profile, Settings, resolve
from dadsops.registry import load_registry
from dadsops.runner import Runner, port_open


def marker_of(launch) -> Path:
    return Path(launch.tool.start_command[3])


def recorded(launch) -> dict:
    return json.loads(marker_of(launch).read_text())


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
def settings(tmp_path: Path) -> Settings:
    return Settings(
        profiles=(Profile("example", "us-east-1"), Profile("example-dev", "eu-west-1")),
        default_profile="example",
        log_dir=str(tmp_path / "logs"),
    )


@pytest.fixture
def load_launch(write_registry, fake_tool_entry, settings):
    def load(entry: dict | None = None, settings: Settings = settings, **kwargs):
        tools = load_registry(write_registry([entry or fake_tool_entry(**kwargs)]))
        (launch,) = resolve(tools, settings)
        return launch

    return load


@pytest.fixture
def make_runner():
    """Runner whose 'port answers' check is the stub's marker file: no sockets involved."""
    runners: list[Runner] = []

    def make(probe=None, start_timeout: float = 5.0) -> Runner:
        def marker_probe(tool):
            return Path(tool.start_command[3]).exists()

        runner = Runner(
            probe=probe or marker_probe, start_timeout=start_timeout, poll_interval=0.05
        )
        runners.append(runner)
        return runner

    yield make
    for runner in runners:
        runner.stop_all()


def test_start_runs_command_when_port_closed(make_runner, load_launch):
    launch = load_launch()
    runner = make_runner()

    result = runner.start(launch)

    assert result.ok and result.started
    assert runner.owns(launch.tool.name)
    assert pid_alive(recorded(launch)["pid"])
    assert launch.tool.name not in runner.errors


def test_started_tool_gets_shared_settings_in_env(make_runner, load_launch, tmp_path):
    launch = load_launch(port=9123)
    make_runner().start(launch)

    env = recorded(launch)["env"]
    assert env["AWS_PROFILE"] == "example"
    assert env["AWS_REGION"] == "us-east-1"
    assert env["DADSOPS_LOG_DIR"] == str(tmp_path / "logs")
    assert "DADSOPS_THEME" not in env
    assert env["PORT"] == "9123"


def test_all_placeholders_and_extra_args_reach_the_child(
    make_runner, load_launch, fake_tool_entry, settings, tmp_path
):
    entry = fake_tool_entry(port=9124)
    entry["start_command"] += [
        "--port",
        "{port}",
        "--bind=127.0.0.1:{port}",
        "--profile={profile}",
        "--region",
        "{region}",
        "--logs={log_dir}",
    ]
    picked = Settings(
        profiles=settings.profiles,
        default_profile=settings.default_profile,
        log_dir=settings.log_dir,
        apps={"tool_a": AppSettings(profile="example-dev", extra_args=("--verbose", "a b"))},
    )
    launch = load_launch(entry, settings=picked)

    assert make_runner().start(launch).ok

    child = recorded(launch)
    assert child["argv"][2:] == [
        "--port",
        "9124",
        "--bind=127.0.0.1:9124",
        "--profile=example-dev",
        "--region",
        "eu-west-1",
        f"--logs={tmp_path / 'logs'}",
        "--verbose",
        "a b",
    ]
    assert child["env"]["AWS_PROFILE"] == "example-dev"
    assert child["env"]["AWS_REGION"] == "eu-west-1"


def test_string_command_placeholder_reaches_child(make_runner, load_launch, fake_tool_entry):
    entry = fake_tool_entry(port=9125)
    entry["start_command"] = shlex.join(entry["start_command"]) + " --port {port}"
    launch = load_launch(entry)

    assert make_runner().start(launch).ok
    assert recorded(launch)["argv"][2:] == ["--port", "9125"]


def test_override_port_is_used_for_the_child(make_runner, load_launch, fake_tool_entry, settings):
    entry = fake_tool_entry(port=9001)
    entry["start_command"] += ["--port", "{port}"]
    overridden = Settings(
        profiles=settings.profiles,
        default_profile=settings.default_profile,
        log_dir=settings.log_dir,
        apps={"tool_a": AppSettings(port=9201)},
    )
    launch = load_launch(entry, settings=overridden)

    assert make_runner().start(launch).ok
    child = recorded(launch)
    assert child["argv"][2:] == ["--port", "9201"]
    assert child["env"]["PORT"] == "9201"


def test_output_goes_to_the_tool_log_in_the_log_dir(make_runner, load_launch, tmp_path):
    launch = load_launch(mode="fail")

    make_runner(probe=lambda _tool: False).start(launch)

    log = tmp_path / "logs" / "tool_a.log"
    assert launch.log_path == log
    assert "boom: stub refused to start" in log.read_text()


def test_disabled_tool_is_not_started(make_runner, load_launch, settings, monkeypatch):
    disabled = Settings(
        profiles=settings.profiles,
        default_profile=settings.default_profile,
        apps={"tool_a": AppSettings(enabled=False)},
    )
    launch = load_launch(settings=disabled)

    def no_spawn(*args, **kwargs):
        raise AssertionError("must not spawn a disabled tool")

    monkeypatch.setattr(runner_module.subprocess, "Popen", no_spawn)
    result = make_runner(probe=lambda _tool: False).start(launch)

    assert not result.ok
    assert result.message == "disabled in settings"


def test_profile_placeholder_without_profile_fails_without_spawning(
    make_runner, load_launch, fake_tool_entry, monkeypatch
):
    entry = fake_tool_entry()
    entry["start_command"] += ["--profile", "{profile}"]
    launch = load_launch(entry, settings=Settings())

    def no_spawn(*args, **kwargs):
        raise AssertionError("must not spawn when the command cannot be built")

    monkeypatch.setattr(runner_module.subprocess, "Popen", no_spawn)
    result = make_runner(probe=lambda _tool: False).start(launch)

    assert not result.ok
    assert "no AWS profile is configured" in result.message


def test_no_second_process_when_port_already_open(make_runner, load_launch, monkeypatch):
    launch = load_launch()
    runner = make_runner(probe=lambda _tool: True)

    def no_spawn(*args, **kwargs):
        raise AssertionError("must not spawn when the port already answers")

    monkeypatch.setattr(runner_module.subprocess, "Popen", no_spawn)

    result = runner.start(launch)

    assert result.ok and not result.started
    assert result.message == "already running"
    assert not runner.owns(launch.tool.name)


def test_no_second_process_for_a_known_pid(make_runner, load_launch):
    launch = load_launch()
    runner = make_runner()
    runner.start(launch)
    first_pid = recorded(launch)["pid"]
    marker_of(launch).unlink()

    result = runner.start(launch)

    assert result.ok and not result.started
    assert not marker_of(launch).exists()
    assert pid_alive(first_pid)


def test_start_that_never_answers_times_out(make_runner, load_launch):
    launch = load_launch(port=9555)
    runner = make_runner(probe=lambda _tool: False, start_timeout=0.5)

    result = runner.start(launch)

    assert not result.ok
    assert "did not answer on port 9555 within 0.5s" in result.message
    assert runner.errors[launch.tool.name] == result.message
    assert not runner.owns(launch.tool.name)
    assert wait_for(marker_of(launch).exists)
    pid = recorded(launch)["pid"]
    assert wait_for(lambda: not pid_alive(pid)), "timed-out process must be cleaned up"


def test_failing_command_reports_exit_code_and_output(make_runner, load_launch):
    launch = load_launch(mode="fail")

    result = make_runner(probe=lambda _tool: False).start(launch)

    assert not result.ok
    assert "exited with code 3" in result.message
    assert "boom: stub refused to start" in result.message


def test_missing_executable_reports_failure(make_runner, load_launch, tool_dirs):
    entry = {"name": "tool_a", "path": str(tool_dirs["tool_a"]), "port": 9001}
    entry["start_command"] = "./no-such-binary"

    result = make_runner(probe=lambda _tool: False).start(load_launch(entry))

    assert not result.ok
    assert result.message.startswith("command failed")


def test_missing_tool_path_is_not_started(make_runner, load_launch, fake_tool_entry, tmp_path):
    entry = fake_tool_entry()
    entry["path"] = str(tmp_path / "nowhere")

    result = make_runner(probe=lambda _tool: False).start(load_launch(entry))

    assert not result.ok
    assert result.message.startswith("not found at")


def test_tool_without_path_starts_from_the_venv_in_the_home_dir(
    make_runner, load_launch, lookup_dirs, make_executable, tmp_path
):
    marker = tmp_path / "tool_a.marker"
    body = (
        f"#!{sys.executable}\n"
        "import json, os, sys, time\n"
        f"open({str(marker)!r}, 'w').write(json.dumps({{'cwd': os.getcwd(), 'argv': sys.argv}}))\n"
        "time.sleep(60)\n"
    )
    exe = make_executable(lookup_dirs["venv_bin"], "tool-a", body)
    entry = {"name": "tool_a", "start_command": "tool-a --port {port}", "port": 9001}
    runner = make_runner(probe=lambda _tool: marker.exists())

    result = runner.start(load_launch(entry))

    assert result.ok and result.started, result.message
    seen = json.loads(marker.read_text())
    assert Path(seen["cwd"]).resolve() == lookup_dirs["home"].resolve()
    assert seen["argv"] == [str(exe), "--port", "9001"]


def test_tool_without_path_and_missing_command_is_not_started(
    make_runner, load_launch, lookup_dirs
):
    entry = {"name": "tool_a", "start_command": "tool-a --port {port}", "port": 9001}

    result = make_runner(probe=lambda _tool: False).start(load_launch(entry))

    assert not result.ok
    assert result.message == "'tool-a' not found on PATH or in the DadsOps venv"


def test_stop_kills_the_process_dadsops_started(make_runner, load_launch):
    launch = load_launch()
    runner = make_runner()
    runner.start(launch)
    pid = recorded(launch)["pid"]

    assert runner.stop(launch.tool) is True
    assert not runner.owns(launch.tool.name)
    assert wait_for(lambda: not pid_alive(pid))


def test_stop_leaves_unrelated_process_alone(make_runner, load_launch, stub_script, tmp_path):
    launch = load_launch()
    unrelated = subprocess.Popen(
        [sys.executable, str(stub_script), "serve", str(tmp_path / "unrelated.marker")]
    )
    try:
        runner = make_runner(probe=lambda _tool: True)  # someone else holds the port

        assert runner.stop(launch.tool) is False
        assert "not started by DadsOps" in runner.errors[launch.tool.name]
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
