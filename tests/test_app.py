import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dadsops.app import create_app
from dadsops.config import Settings, load_settings, save_settings
from dadsops.runner import Runner, StartResult


class FakeRunner(Runner):
    """Records start/stop calls instead of spawning anything."""

    def __init__(self, tmp_path: Path, start_result: StartResult | None = None):
        super().__init__(log_dir=tmp_path / "logs", probe=lambda _tool: False)
        self.start_result = start_result or StartResult(ok=True, started=True, message="ok")
        self.started: list[tuple[str, Settings]] = []

    def start(self, tool, settings):
        self.started.append((tool.name, settings))
        if not self.start_result.ok:
            self.errors[tool.name] = self.start_result.message
        return self.start_result


@pytest.fixture
def registry(write_registry, tool_dirs) -> Path:
    return write_registry(
        [
            {
                "name": "tool_a",
                "description": "Does A things.",
                "path": str(tool_dirs["tool_a"]),
                "start_command": "./run.sh",
                "port": 9001,
            },
            {"name": "tool_b", "path": "missing", "start_command": "./run.sh", "port": 9002},
            {
                "name": "tool_c",
                "path": str(tool_dirs["tool_b"]),
                "start_command": "./run.sh",
                "port": 9003,
                "coming_soon": True,
            },
        ]
    )


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    return tmp_path / "config" / "config.yaml"


@pytest.fixture
def fake_runner(tmp_path: Path) -> FakeRunner:
    return FakeRunner(tmp_path)


@pytest.fixture
def client(registry, config_file, fake_runner):
    with TestClient(create_app(registry, config_file, runner=fake_runner)) as test_client:
        yield test_client


def test_home_renders_one_card_per_tool_with_status(client):
    page = client.get("/")

    assert page.status_code == 200
    cards = re.findall(r'<article class="card status-([a-z-]+)" data-tool="(\w+)"', page.text)
    assert cards == [("installed", "tool_a"), ("not-found", "tool_b"), ("coming-soon", "tool_c")]
    assert '<span class="badge badge-installed">installed</span>' in page.text
    assert '<span class="badge badge-not-found">not found</span>' in page.text
    assert '<span class="badge badge-coming-soon">coming soon</span>' in page.text
    assert "Does A things." in page.text
    assert 'href="/open/tool_a"' in page.text
    assert 'href="/open/tool_b"' not in page.text


def test_invalid_registry_shows_message_instead_of_crashing(config_file, fake_runner, tmp_path):
    broken = tmp_path / "broken.yaml"
    broken.write_text("tools:\n  - name: tool_a\n")
    with TestClient(create_app(broken, config_file, runner=fake_runner)) as client:
        page = client.get("/")
        opened = client.get("/open/tool_a", follow_redirects=False)

    assert page.status_code == 200
    assert "Tool registry could not be loaded" in page.text
    assert "missing required key(s)" in page.text
    assert opened.headers["location"] == "/"


def test_open_starts_tool_and_redirects_to_it(client, fake_runner, config_file):
    save_settings(Settings(aws_profile="example", theme="dark"), config_file)

    response = client.get("/open/tool_a", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "http://127.0.0.1:9001/"
    assert fake_runner.started == [("tool_a", load_settings(config_file))]


def test_open_failure_returns_home_with_reason(registry, config_file, tmp_path):
    runner = FakeRunner(tmp_path, StartResult(ok=False, started=False, message="did not answer"))
    with TestClient(create_app(registry, config_file, runner=runner)) as client:
        response = client.get("/open/tool_a", follow_redirects=False)
        page = client.get("/")

    assert response.headers["location"] == "/"
    assert '<p class="failure" role="alert">did not answer</p>' in page.text


def test_open_unknown_tool_is_404(client):
    assert client.get("/open/nope", follow_redirects=False).status_code == 404


def test_stop_refuses_unowned_and_rejects_cross_origin(client):
    response = client.post("/stop/tool_a", follow_redirects=False)
    assert response.status_code == 303
    assert "not started by DadsOps" in client.get("/").text

    cross = client.post("/stop/tool_a", headers={"origin": "http://192.0.2.1"})
    assert cross.status_code == 403


def test_settings_page_saves_atomically_to_config(client, config_file):
    assert client.get("/settings").status_code == 200

    response = client.post(
        "/settings",
        content="aws_profile=example&aws_region=eu-central-1&theme=dark",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert load_settings(config_file) == Settings("example", "eu-central-1", "dark")
    assert 'class="theme-dark"' in client.get("/").text


def test_settings_page_rejects_invalid_values(client, config_file):
    response = client.post(
        "/settings",
        content="aws_profile=bad+profile&aws_region=us-east-1&theme=light",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 400
    assert "AWS profile may use" in response.text
    assert not config_file.exists()
