import shutil
from pathlib import Path

import pytest

from dadsops.registry import RegistryError, load_registry

FIXTURES = Path(__file__).parent / "fixtures"


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


def test_port_placeholder_is_substituted_in_string_and_list_commands(valid_registry: Path):
    tool_a, tool_b, tool_c = load_registry(valid_registry)

    assert tool_a.command() == (".venv/bin/python", "-m", "tool_a", "--port", "9001")
    assert tool_b.command() == ("./run.sh", "--flag", "--listen=127.0.0.1:9002")
    assert tool_c.command() == ("./run.sh",)


def test_port_substitution_is_exact_and_leaves_other_text_alone(write_registry):
    command = ["run", "{port}{port}", "{ports}", "{Port}", "{other}", "{{port}}", "a b"]
    (tool,) = load_registry(write_registry([base_entry(start_command=command)]))

    assert tool.command() == ("run", "90019001", "{ports}", "{Port}", "{other}", "{9001}", "a b")
    assert not any("{port}" in part for part in tool.command())


def test_repo_registry_loads(tmp_path: Path):
    path = tmp_path / "tools.yaml"
    shutil.copy(Path(__file__).parents[1] / "tools.yaml", path)

    tools = {tool.name: tool for tool in load_registry(path)}

    assert {name: tool.port for name, tool in tools.items()} == {
        "EC2Patcher": 8081,
        "IPLens": 8082,
        "TFLens": 8083,
        "RoleLens": 8084,
    }
    assert tools["TFLens"].path == (tmp_path.parent / "tflens").resolve()
    assert tools["TFLens"].description == "Explain Terraform repos: resources, access, code refs"
    assert tools["TFLens"].command()[-3:] == ("serve", "--port", "8083")
    for tool in tools.values():
        assert tool.command()[-2:] == ("--port", str(tool.port))
        assert not any("{port}" in part for part in tool.command())


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
        ([base_entry(path=None)], "missing required key(s): path"),
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
        ([base_entry(path="")], "path must be"),
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
