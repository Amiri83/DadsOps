import os
import shutil
from pathlib import Path

import pytest
import yaml

from dadsops import registry as registry_module
from dadsops.config import Profile, Settings, resolve
from dadsops.paths import DEFAULT_REGISTRY
from dadsops.registry import RegistryError, load_registry

FIXTURES = Path(__file__).parent / "fixtures"
REPO_REGISTRY = Path(__file__).parents[1] / "tools.yaml"
VALUES = {"port": "9001", "profile": "example", "region": "eu-west-1", "log_dir": "/tmp/logs"}


@pytest.fixture
def valid_registry(tmp_path: Path) -> Path:
    path = tmp_path / "tools.yaml"
    shutil.copy(FIXTURES / "registry_valid.yaml", path)
    (tmp_path / "tool_a").mkdir()
    return path


def test_loads_all_fields(valid_registry: Path):
    tool_a, tool_b, tool_c = load_registry(valid_registry)

    assert tool_a.name == "tool_a"
    assert tool_a.description == "First example tool."
    assert tool_a.path == valid_registry.parent / "tool_a"
    assert tool_a.start_command == (".venv/bin/python", "-m", "tool_a", "--port", "{port}")
    assert tool_a.port == 9001
    assert tool_a.health_path == "/healthz"
    assert tool_b.start_command == ("./run.sh", "--flag", "--listen=127.0.0.1:{port}")
    assert tool_b.health_path is None
    assert tool_c.coming_soon is True


def test_placeholders_are_substituted_in_string_and_list_commands(valid_registry: Path):
    tool_a, tool_b, tool_c = load_registry(valid_registry)

    assert tool_a.command(VALUES) == (".venv/bin/python", "-m", "tool_a", "--port", "9001")
    assert tool_b.command(VALUES) == ("./run.sh", "--flag", "--listen=127.0.0.1:9001")
    assert tool_c.command(VALUES) == ("./run.sh",)


def test_all_four_placeholders_are_substituted(write_registry):
    command = ["run", "{port}{port}", "--profile={profile}", "{region}", "--logs", "{log_dir}"]
    (tool,) = load_registry(write_registry([base_entry(start_command=command)]))

    assert tool.placeholders() == {"port", "profile", "region", "log_dir"}
    assert tool.command(VALUES) == (
        "run",
        "90019001",
        "--profile=example",
        "eu-west-1",
        "--logs",
        "/tmp/logs",
    )


def test_text_that_is_not_a_placeholder_is_left_alone(write_registry):
    command = ["run", "{Port", "port}", "{}", '{"a": 1}', "a b"]
    (tool,) = load_registry(write_registry([base_entry(start_command=command)]))

    assert tool.command(VALUES) == tuple(command)


@pytest.mark.parametrize("placeholder", ["{ports}", "{Port}", "{other}", "{token}"])
def test_unknown_placeholder_is_rejected_at_load(write_registry, placeholder):
    entry = base_entry(start_command=f"./run.sh --x {placeholder}")

    with pytest.raises(RegistryError, match="unknown placeholder"):
        load_registry(write_registry([entry]))


def test_repo_registry_loads(tmp_path: Path):
    path = tmp_path / "tools.yaml"
    shutil.copy(REPO_REGISTRY, path)

    tools = {tool.name: tool for tool in load_registry(path)}

    assert {name: tool.port for name, tool in tools.items()} == {
        "EC2Patcher": 8081,
        "IPLens": 8082,
        "TFLens": 8083,
        "RoleLens": 8084,
    }
    assert all(tool.path is None for tool in tools.values()), "a clone alone must work"
    assert tools["TFLens"].description == "Explain Terraform repos: resources, access, code refs"
    for tool in tools.values():
        assert tool.command({"port": str(tool.port)})[-2:] == ("--port", str(tool.port))


def test_packaged_default_lists_the_same_tools_as_the_checkout_registry():
    def summary(path: Path) -> list[tuple]:
        return [
            (t.name, t.port, t.description, t.coming_soon, t.start_command, t.package)
            for t in load_registry(path)
        ]

    assert summary(DEFAULT_REGISTRY) == summary(REPO_REGISTRY)


def test_packaged_default_uses_console_scripts_and_no_paths():
    raw = yaml.safe_load(DEFAULT_REGISTRY.read_text())
    assert all("path" not in entry for entry in raw["tools"])

    tools = {tool.name: tool for tool in load_registry(DEFAULT_REGISTRY)}

    assert all(tool.path is None for tool in tools.values())
    assert {name: " ".join(t.start_command) for name, t in tools.items() if not t.coming_soon} == {
        "EC2Patcher": "ec2patcher --port {port}",
        "IPLens": "iplens --port {port}",
        "TFLens": "tflens serve --port {port}",
    }
    assert {name: tool.port for name, tool in tools.items()} == {
        "EC2Patcher": 8081,
        "IPLens": 8082,
        "TFLens": 8083,
        "RoleLens": 8084,
    }
    assert tools["RoleLens"].coming_soon is True


def test_status_comes_from_the_filesystem(valid_registry: Path):
    tool_a, tool_b, tool_c = load_registry(valid_registry)

    assert tool_a.status == "installed"
    assert tool_b.status == "not found"
    assert tool_c.status == "coming soon"

    (valid_registry.parent / "missing_tool_b").mkdir()
    assert tool_b.status == "installed"


def base_entry(**overrides) -> dict:
    entry = {"name": "tool_a", "path": "tool_a", "start_command": "./run.sh", "port": 9001}
    entry.update(overrides)
    return {k: v for k, v in entry.items() if v is not None}


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        ([base_entry(), base_entry(port=9002)], "duplicate name"),
        ([base_entry(), base_entry(name="tool_b")], "duplicate port"),
        ([base_entry(name=None)], "missing required key(s): name"),
        ([base_entry(start_command=None)], "missing required key(s): start_command"),
        ([base_entry(port=None)], "missing required key(s): port"),
        ([base_entry(port=0)], "port must be"),
        ([base_entry(port=70000)], "port must be"),
        ([base_entry(port="9001")], "port must be"),
        ([base_entry(port=True)], "port must be"),
        ([base_entry(start_command="")], "start_command must not be empty"),
        ([base_entry(start_command="   ")], "start_command must not be empty"),
        ([base_entry(start_command=[])], "start_command must not be empty"),
        ([base_entry(start_command=["", "x"])], "start_command must not be empty"),
        ([base_entry(name="tool a")], "name must use only"),
        ([base_entry(name="../tool_a")], "name must use only"),
        ([base_entry(path="")], "path must be"),
        ([base_entry(path="  ")], "path must be"),
        ([base_entry(path=5)], "path must be"),
        ([base_entry(health_path="healthz")], "health_path must start"),
        ([base_entry(coming_soon="yes")], "coming_soon must be"),
        ([base_entry(colour="red")], "unknown key(s): colour"),
    ],
)
def test_rejects_invalid_entries(write_registry, entries, message):
    with pytest.raises(RegistryError, match=message.replace("(", r"\(").replace(")", r"\)")):
        load_registry(write_registry(entries))


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("tools: [unclosed", "not valid YAML"),
        ("just a string", "'tools:' list"),
        ("tools: {}", "'tools:' list"),
    ],
)
def test_rejects_malformed_files(tmp_path: Path, content: str, message: str):
    path = tmp_path / "tools.yaml"
    path.write_text(content)
    with pytest.raises(RegistryError, match=message):
        load_registry(path)


def test_missing_file_is_a_registry_error(tmp_path: Path):
    with pytest.raises(RegistryError, match="cannot read"):
        load_registry(tmp_path / "absent.yaml")


def test_missing_tool_path_is_not_an_error(write_registry):
    (tool,) = load_registry(write_registry([base_entry(path="does_not_exist")]))
    assert tool.status == "not found"


# ---------------------------------------------------------------- tools without a path


def test_entry_with_existing_path_is_installed_from_the_checkout(
    write_registry, tool_dirs, lookup_dirs
):
    (tool,) = load_registry(write_registry([base_entry(path=str(tool_dirs["tool_a"]))]))

    location = tool.locate()
    assert tool.status == "installed"
    assert (location.kind, location.where) == ("checkout", tool_dirs["tool_a"])
    assert location.label == f"found: {tool_dirs['tool_a']}"
    assert tool.workdir == tool_dirs["tool_a"]


def test_entry_with_missing_path_is_not_found_even_if_the_command_exists(
    write_registry, lookup_dirs, make_executable, tmp_path
):
    make_executable(lookup_dirs["path_bin"], "tool-a")
    entry = base_entry(path=str(tmp_path / "nowhere"), start_command="tool-a")
    (tool,) = load_registry(write_registry([entry]))

    assert tool.status == "not found"
    assert tool.locate().label == "not found"
    assert tool.missing_reason() == f"not found at {tmp_path / 'nowhere'}"


def test_checkout_under_home_is_shown_with_a_tilde(write_registry, lookup_dirs):
    checkout = lookup_dirs["home"] / "ToolA"
    checkout.mkdir()
    (tool,) = load_registry(write_registry([base_entry(path="~/ToolA")]))

    assert tool.path == checkout
    assert tool.locate().label == "found: ~/ToolA"


def test_no_path_and_command_on_path_is_installed(write_registry, lookup_dirs, make_executable):
    exe = make_executable(lookup_dirs["path_bin"], "tool-a")
    (tool,) = load_registry(write_registry([base_entry(path=None, start_command="tool-a")]))

    location = tool.locate()
    assert tool.path is None
    assert tool.status == "installed"
    assert (location.kind, location.where) == ("path", exe)
    assert location.label == "found on PATH"
    assert location.detail == f"on PATH: {exe}"


def test_no_path_and_command_only_in_the_venv_is_installed(
    write_registry, lookup_dirs, make_executable
):
    exe = make_executable(lookup_dirs["venv_bin"], "tool-a")
    (tool,) = load_registry(write_registry([base_entry(path=None, start_command="tool-a")]))

    location = tool.locate()
    assert tool.status == "installed"
    assert (location.kind, location.where) == ("venv", exe)
    assert location.label == "found in venv"
    assert location.detail == f"in the DadsOps venv: {exe}"


def test_command_on_path_inside_the_venv_bin_is_reported_as_venv(
    write_registry, lookup_dirs, make_executable, monkeypatch
):
    make_executable(lookup_dirs["venv_bin"], "tool-a")
    monkeypatch.setenv("PATH", os.pathsep.join(map(str, lookup_dirs.values())))
    (tool,) = load_registry(write_registry([base_entry(path=None, start_command="tool-a")]))

    assert tool.locate().kind == "venv"


def test_no_path_and_command_missing_is_not_found(write_registry, lookup_dirs, make_executable):
    make_executable(lookup_dirs["path_bin"], "tool-b")
    (tool,) = load_registry(write_registry([base_entry(path=None, start_command="tool-a")]))

    assert tool.status == "not found"
    assert not tool.installed
    assert tool.locate().label == "not found"
    assert tool.missing_reason() == "'tool-a' not found in the DadsOps venv or on PATH"
    assert tool.install_command is None


def test_missing_console_script_names_its_pip_distribution(write_registry, lookup_dirs):
    entry = base_entry(path=None, start_command="tool-a", package="example-tool-a")
    (tool,) = load_registry(write_registry([entry]))

    assert tool.install_command == "pip install example-tool-a"
    assert tool.missing_reason() == (
        "'tool-a' not found in the DadsOps venv or on PATH; "
        "install it with: pip install example-tool-a"
    )


@pytest.mark.parametrize("package", ["", "-bad", "two words", "../tool_a", 7])
def test_invalid_package_is_rejected(write_registry, package):
    with pytest.raises(RegistryError, match="package must be a PyPI distribution name"):
        load_registry(write_registry([base_entry(package=package)]))


def test_venv_bin_is_searched_before_path(write_registry, lookup_dirs, make_executable):
    """The bundled copy next to DadsOps wins over another install of the same command."""
    make_executable(lookup_dirs["path_bin"], "tool-a")
    venv_exe = make_executable(lookup_dirs["venv_bin"], "tool-a")
    (tool,) = load_registry(write_registry([base_entry(path=None, start_command="tool-a")]))

    assert (tool.locate().kind, tool.locate().where) == ("venv", venv_exe)


def test_non_executable_file_on_path_is_not_found(write_registry, lookup_dirs):
    (lookup_dirs["path_bin"] / "tool-a").write_text("not executable")
    (tool,) = load_registry(write_registry([base_entry(path=None, start_command="tool-a")]))

    assert tool.status == "not found"


def test_coming_soon_wins_over_location(write_registry, lookup_dirs, make_executable):
    make_executable(lookup_dirs["path_bin"], "tool-a")
    entry = base_entry(path=None, start_command="tool-a", coming_soon=True)
    (tool,) = load_registry(write_registry([entry]))

    assert tool.status == "coming soon"


def test_no_path_placeholders_are_substituted_and_workdir_is_home(
    write_registry, lookup_dirs, make_executable
):
    exe = make_executable(lookup_dirs["venv_bin"], "tool-a")
    command = "tool-a serve --port {port} --profile {profile} --region {region} --logs {log_dir}"
    (tool,) = load_registry(write_registry([base_entry(path=None, start_command=command)]))
    settings = Settings(
        profiles=(Profile("example", "eu-west-1"),),
        default_profile="example",
        log_dir="/var/tmp/dadsops-logs",
    )
    (launch,) = resolve([tool], settings)

    assert tool.command(VALUES)[:4] == ("tool-a", "serve", "--port", "9001")
    assert launch.command() == (
        str(exe),
        "serve",
        "--port",
        "9001",
        "--profile",
        "example",
        "--region",
        "eu-west-1",
        "--logs",
        "/var/tmp/dadsops-logs",
    )
    assert launch.display_command().startswith(f"{exe} serve --port 9001")
    assert tool.workdir == lookup_dirs["home"]
    assert tool.workdir == Path(os.path.expanduser("~"))


def test_no_path_command_that_is_not_found_keeps_its_name(write_registry, lookup_dirs):
    entry = base_entry(path=None, start_command="tool-a --port {port}")
    (tool,) = load_registry(write_registry([entry]))
    (launch,) = resolve([tool], Settings())

    assert launch.command() == ("tool-a", "--port", "9001")


def test_registry_module_defaults_to_the_running_interpreters_bin():
    assert Path(registry_module.VENV_BIN) == Path(registry_module.sys.executable).parent
