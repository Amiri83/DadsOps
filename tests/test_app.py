import re
from pathlib import Path
from urllib.parse import urlencode

import pytest
import yaml
from fastapi.testclient import TestClient

from dadsops.app import create_app
from dadsops.config import AppSettings, Profile, Settings, load_settings, save_settings
from dadsops.runner import Runner, StartResult

SAME_ORIGIN = {"origin": "http://testserver"}
FORM = {"content-type": "application/x-www-form-urlencoded", **SAME_ORIGIN}


class FakeRunner(Runner):
    """Records start/stop calls instead of spawning anything."""

    def __init__(self, start_result: StartResult | None = None):
        super().__init__(probe=lambda _tool: False)
        self.start_result = start_result or StartResult(ok=True, started=True, message="ok")
        self.started: list = []

    def start(self, launch):
        self.started.append(launch)
        if not self.start_result.ok:
            self.errors[launch.tool.name] = self.start_result.message
        return self.start_result


@pytest.fixture
def registry(write_registry, tool_dirs) -> Path:
    return write_registry(
        [
            {
                "name": "tool_a",
                "description": "Does A things.",
                "path": str(tool_dirs["tool_a"]),
                "start_command": "./run.sh --port {port}",
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
def log_dir(tmp_path: Path) -> Path:
    return tmp_path / "logs"


@pytest.fixture
def saved_settings(config_file, log_dir) -> Settings:
    settings = Settings(
        profiles=(Profile("example", "us-east-1"), Profile("example-dev", "eu-west-1")),
        default_profile="example",
        log_dir=str(log_dir),
    )
    save_settings(settings, config_file)
    return settings


@pytest.fixture
def fake_runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def client(registry, config_file, fake_runner):
    with TestClient(create_app(registry, config_file, runner=fake_runner)) as test_client:
        yield test_client


def form_body(**overrides) -> str:
    """A full settings form as the page submits it: two profiles plus the blank new row."""
    fields = {
        "log_dir": "/var/tmp/dadsops-logs",
        "profile_name": ["example", "example-dev", ""],
        "profile_region": ["us-east-1", "eu-west-1", ""],
        "default_profile": "0",
        "app": ["tool_a", "tool_b", "tool_c"],
        "enabled.tool_a": "on",
        "enabled.tool_b": "on",
        "enabled.tool_c": "on",
        "port.tool_a": "",
        "port.tool_b": "",
        "port.tool_c": "",
        "profile.tool_a": "",
        "profile.tool_b": "",
        "profile.tool_c": "",
        "args.tool_a": "",
        "args.tool_b": "",
        "args.tool_c": "",
    }
    fields.update(overrides)
    return urlencode({k: v for k, v in fields.items() if v is not None}, doseq=True)


def post_settings(client, **overrides):
    return client.post(
        "/settings", content=form_body(**overrides), headers=FORM, follow_redirects=False
    )


def test_home_renders_one_card_per_tool_with_status(client):
    page = client.get("/")

    assert page.status_code == 200
    cards = re.findall(r'<article class="card status-([a-z-]+)" data-tool="(\w+)"', page.text)
    assert cards == [("installed", "tool_a"), ("not-found", "tool_b"), ("coming-soon", "tool_c")]
    assert "Does A things." in page.text
    assert '<form class="open" method="post" action="/open/tool_a">' in page.text
    assert 'action="/open/tool_b"' not in page.text


def test_invalid_registry_shows_message_instead_of_crashing(config_file, fake_runner, tmp_path):
    broken = tmp_path / "broken.yaml"
    broken.write_text("tools:\n  - name: tool_a\n")
    with TestClient(create_app(broken, config_file, runner=fake_runner)) as client:
        page = client.get("/")
        opened = client.post("/open/tool_a", headers=SAME_ORIGIN, follow_redirects=False)
        logs = client.get("/logs")

    assert page.status_code == 200
    assert "Tool registry could not be loaded" in page.text
    assert "missing required key(s)" in page.text
    assert opened.headers["location"] == "/"
    assert "No tools in the registry" in logs.text


def test_open_starts_tool_with_settings_and_redirects_to_it(client, fake_runner, saved_settings):
    response = client.post("/open/tool_a", headers=SAME_ORIGIN, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "http://127.0.0.1:9001/"
    (launch,) = fake_runner.started
    assert launch.tool.name == "tool_a"
    assert launch.profile == Profile("example", "us-east-1")
    assert launch.log_dir == Path(saved_settings.log_dir)


def test_open_uses_the_per_app_port_over_the_registry(client, fake_runner, config_file):
    save_settings(Settings(apps={"tool_a": AppSettings(port=9101)}), config_file)

    response = client.post("/open/tool_a", headers=SAME_ORIGIN, follow_redirects=False)

    assert response.headers["location"] == "http://127.0.0.1:9101/"
    assert fake_runner.started[0].command() == ("./run.sh", "--port", "9101")


def test_disabled_tool_shows_as_disabled(client, config_file):
    save_settings(Settings(apps={"tool_a": AppSettings(enabled=False)}), config_file)

    page = client.get("/")

    assert '<article class="card status-disabled" data-tool="tool_a">' in page.text
    assert 'action="/open/tool_a"' not in page.text


def test_open_with_a_broken_settings_file_does_not_start(client, fake_runner, config_file):
    config_file.parent.mkdir(parents=True)
    config_file.write_text("log_dir: ''\n")

    client.post("/open/tool_a", headers=SAME_ORIGIN, follow_redirects=False)

    assert fake_runner.started == []
    assert "settings are invalid" in client.get("/").text


def test_open_failure_returns_home_with_reason(registry, config_file):
    runner = FakeRunner(StartResult(ok=False, started=False, message="did not answer"))
    with TestClient(create_app(registry, config_file, runner=runner)) as client:
        response = client.post("/open/tool_a", headers=SAME_ORIGIN, follow_redirects=False)
        page = client.get("/")

    assert response.headers["location"] == "/"
    assert '<p class="failure" role="alert">did not answer</p>' in page.text


def test_open_unknown_tool_is_404(client):
    response = client.post("/open/nope", headers=SAME_ORIGIN, follow_redirects=False)
    assert response.status_code == 404


def test_open_accepts_same_origin_referer(client, fake_runner):
    response = client.post(
        "/open/tool_a", headers={"referer": "http://testserver/"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert [launch.tool.name for launch in fake_runner.started] == ["tool_a"]


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"origin": "http://192.0.2.1"},
        {"origin": "http://testserver.example"},
        {"origin": "null"},
        {"referer": "http://192.0.2.1/open/tool_a"},
        {"origin": "http://192.0.2.1", "referer": "http://testserver/"},
    ],
)
def test_open_rejects_missing_or_foreign_origin(client, fake_runner, headers):
    response = client.post("/open/tool_a", headers=headers, follow_redirects=False)

    assert response.status_code == 403
    assert fake_runner.started == []


def test_get_open_does_not_start_anything(client, fake_runner):
    response = client.get("/open/tool_a", follow_redirects=False)

    assert response.status_code == 405
    assert fake_runner.started == []


def test_stop_refuses_unowned_and_rejects_cross_origin(client):
    response = client.post("/stop/tool_a", headers=SAME_ORIGIN, follow_redirects=False)
    assert response.status_code == 303
    assert "not started by DadsOps" in client.get("/").text

    assert client.post("/stop/tool_a", headers={"origin": "http://192.0.2.1"}).status_code == 403
    assert client.post("/stop/tool_a").status_code == 403


def test_settings_page_shows_profiles_and_one_row_per_app(client, saved_settings):
    page = client.get("/settings")

    assert page.status_code == 200
    assert page.text.count('name="profile_name"') == 3, "two profiles plus the blank new row"
    assert re.search(r'name="default_profile" value="0"[^>]*checked', page.text)
    assert re.findall(r'<tr data-app="(\w+)">', page.text) == ["tool_a", "tool_b", "tool_c"]
    assert 'placeholder="9001"' in page.text


def test_settings_page_saves_everything(client, config_file):
    response = post_settings(
        client,
        default_profile="1",
        **{
            "port.tool_a": "9101",
            "profile.tool_a": "example-dev",
            "args.tool_a": "--verbose --name 'a b'",
            "enabled.tool_b": None,
        },
    )

    assert response.status_code == 303
    assert load_settings(config_file) == Settings(
        profiles=(Profile("example", "us-east-1"), Profile("example-dev", "eu-west-1")),
        default_profile="example-dev",
        log_dir="/var/tmp/dadsops-logs",
        apps={
            "tool_a": AppSettings(
                port=9101, profile="example-dev", extra_args=("--verbose", "--name", "a b")
            ),
            "tool_b": AppSettings(enabled=False),
        },
    )


def test_settings_page_adds_and_removes_profiles(client, config_file, saved_settings):
    response = post_settings(
        client,
        profile_name=["example", "example-dev", "example-ops"],
        profile_region=["us-east-1", "eu-west-1", "ap-southeast-2"],
        profile_remove="0",
        default_profile="2",
    )

    assert response.status_code == 303
    settings = load_settings(config_file)
    assert settings.profiles == (
        Profile("example-dev", "eu-west-1"),
        Profile("example-ops", "ap-southeast-2"),
    )
    assert settings.default_profile == "example-ops"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"port.tool_a": "9005", "port.tool_b": "9005"}, "port 9005 is already used by tool_a"),
        ({"port.tool_a": "9002"}, "port 9002 is already used by tool_a"),
        ({"port.tool_a": "70000"}, "1 to 65535"),
        ({"port.tool_a": "eighty"}, "whole number"),
        ({"profile.tool_a": "ghost"}, "not in the profile list"),
        ({"args.tool_a": "--name 'unclosed"}, "extra args cannot be parsed"),
        ({"args.tool_a": "--token=abc"}, "looks like a secret"),
        ({"default_profile": None}, "exactly one AWS profile"),
        ({"profile_region": ["us-east-1", "Mars", ""]}, "region must look like"),
        ({"log_dir": ""}, "log directory"),
    ],
)
def test_invalid_submit_shows_reason_and_keeps_the_file(
    client, config_file, saved_settings, overrides, message
):
    before = config_file.read_bytes()

    response = post_settings(client, **overrides)

    assert response.status_code == 400
    assert "Not saved:" in response.text
    assert message in response.text.replace("&#39;", "'")
    assert config_file.read_bytes() == before


def test_rejected_submit_redisplays_what_was_typed(client, saved_settings):
    response = post_settings(client, **{"port.tool_a": "eighty", "args.tool_a": "--verbose"})

    assert 'value="eighty"' in response.text
    assert 'value="--verbose"' in response.text


def test_settings_post_rejects_cross_origin(client, config_file):
    response = client.post(
        "/settings",
        content=form_body(),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 403
    assert not config_file.exists()


def test_logs_page_shows_the_last_200_lines_per_tool(client, saved_settings, log_dir):
    log_dir.mkdir()
    (log_dir / "tool_a.log").write_text("".join(f"tool_a line {i}\n" for i in range(1, 301)))

    page = client.get("/logs")

    assert page.status_code == 200
    assert re.findall(r'<section class="log" data-tool="(\w+)">', page.text) == [
        "tool_a",
        "tool_b",
        "tool_c",
    ]
    assert "tool_a line 101\n" in page.text
    assert "tool_a line 100\n" not in page.text
    assert "tool_a line 300" in page.text
    assert page.text.count("No output yet") == 2


def test_logs_page_empty_state(client, saved_settings):
    page = client.get("/logs")

    assert page.status_code == 200
    assert page.text.count("No output yet") == 3
    assert "<pre>" not in page.text


def test_single_tool_log_page(client, saved_settings, log_dir):
    log_dir.mkdir()
    (log_dir / "tool_b.log").write_text("hello from tool_b\n")

    page = client.get("/logs/tool_b")

    assert page.status_code == 200
    assert "hello from tool_b" in page.text
    assert 'data-tool="tool_a"' not in page.text


@pytest.mark.parametrize("name", ["%2E%2E", "%2E%2E%5Ctool_a", "tool_a.log", "%2Eenv"])
def test_traversal_shaped_tool_name_is_rejected(client, saved_settings, tmp_path, name):
    (tmp_path / "secret.log").write_text("must not leak")

    response = client.get(f"/logs/{name}")

    assert response.status_code == 400
    assert "must not leak" not in response.text


def test_unknown_tool_log_is_404(client, saved_settings):
    assert client.get("/logs/tool_z").status_code == 404


def test_settings_file_holds_no_secret_shaped_keys(client, config_file):
    post_settings(client)

    stored = yaml.safe_load(config_file.read_text())
    flat = repr(stored).lower()
    assert set(stored) == {"log_dir", "profiles", "apps"}
    assert not any(word in flat for word in ("secret", "token", "access_key", "password"))


# ---------------------------------------------------------------- shared theme

THEME_LINK = '<link rel="stylesheet" href="/static/theme/dadsops-theme.css">'


@pytest.mark.parametrize("url", ["/", "/settings", "/logs", "/logs/tool_a", "/logs/tool_z"])
def test_every_page_links_the_theme_before_app_css(client, saved_settings, url):
    page = client.get(url)

    assert THEME_LINK in page.text
    assert page.text.index(THEME_LINK) < page.text.index('href="/static/style.css"')
    assert "theme-light" not in page.text
    assert "theme-dark" not in page.text


def test_theme_file_is_served(client):
    response = client.get("/static/theme/dadsops-theme.css")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    first_line = response.text.splitlines()[0]
    assert first_line == (
        "/* DadsOps shared theme v1 — master copy, tools copy this file unchanged */"
    )
    assert "--bg:" in response.text


def test_theme_file_is_self_contained(client):
    css = client.get("/static/theme/dadsops-theme.css").text

    assert "@import" not in css
    assert "url(" not in css
    assert "http" not in css


@pytest.mark.parametrize(
    ("url", "label"), [("/", "Home"), ("/settings", "Settings"), ("/logs", "Logs")]
)
def test_top_menu_highlights_the_current_page(client, url, label):
    page = client.get(url).text

    nav = page[page.index("<nav") : page.index("</nav>")]
    assert re.findall(r">(Home|Settings|Logs)</a>", nav) == ["Home", "Settings", "Logs"]
    assert re.findall(r'class="active" aria-current="page">(\w+)</a>', nav) == [label]


def test_error_pages_use_the_theme(client):
    page = client.get("/logs/tool_z")

    assert page.status_code == 404
    assert "unknown tool: tool_z" in page.text
    assert THEME_LINK in page.text
    assert 'class="active"' not in page.text


def test_settings_page_has_no_theme_control(client):
    page = client.get("/settings").text

    assert 'name="theme"' not in page
    assert "Theme" not in page


def test_legacy_theme_key_in_config_still_renders(client, config_file):
    config_file.parent.mkdir(parents=True)
    config_file.write_text("theme: dark\n")

    page = client.get("/")

    assert page.status_code == 200
    assert "Settings problem" not in page.text


# ---------------------------------------------------------------- tools installed from PyPI


@pytest.fixture
def pypi_client(write_registry, config_file, fake_runner, lookup_dirs, make_executable, tool_dirs):
    make_executable(lookup_dirs["path_bin"], "tool-a")
    make_executable(lookup_dirs["venv_bin"], "tool-b")
    registry = write_registry(
        [
            {"name": "ToolA", "start_command": "tool-a --port {port}", "port": 9001},
            {"name": "ToolB", "start_command": "tool-b serve --port {port}", "port": 9002},
            {"name": "ToolC", "start_command": "tool-c --port {port}", "port": 9003},
            {
                "name": "ToolD",
                "path": str(tool_dirs["tool_a"]),
                "start_command": "./run.sh",
                "port": 9004,
            },
        ],
        name="pypi_tools.yaml",
    )
    with TestClient(create_app(registry, config_file, runner=fake_runner)) as test_client:
        yield test_client


def card_html(page: str, name: str) -> str:
    start = page.index(f'data-tool="{name}"')
    return page[start : page.index("</article>", start)]


def test_cards_show_where_each_tool_was_found(pypi_client, tool_dirs):
    page = pypi_client.get("/").text

    cards = re.findall(r'<article class="card status-([a-z-]+)" data-tool="(\w+)"', page)
    assert cards == [
        ("installed", "ToolA"),
        ("installed", "ToolB"),
        ("not-found", "ToolC"),
        ("installed", "ToolD"),
    ]
    assert "found on PATH" in card_html(page, "ToolA")
    assert "found in venv" in card_html(page, "ToolB")
    assert f"found: {tool_dirs['tool_a']}" in card_html(page, "ToolD")


def test_not_found_tool_has_no_start_button(pypi_client):
    page = pypi_client.get("/")

    assert page.status_code == 200
    card = card_html(page.text, "ToolC")
    assert "not found" in card
    assert "/open/" not in card
    assert "Start" not in card
    assert 'action="/open/ToolA"' in page.text


def test_settings_page_shows_location_and_resolved_command(pypi_client, lookup_dirs):
    page = pypi_client.get("/settings").text
    tool_a = lookup_dirs["path_bin"] / "tool-a"
    tool_b = lookup_dirs["venv_bin"] / "tool-b"

    def row(name: str) -> str:
        start = page.index(f'<tr data-app="{name}">')
        return page[start : page.index("</tr>", start)]

    assert "found on PATH" in row("ToolA")
    assert f"on PATH: {tool_a}" in row("ToolA")
    assert f"<code>{tool_a} --port 9001</code>" in row("ToolA")
    assert "found in venv" in row("ToolB")
    assert f"in the DadsOps venv: {tool_b}" in row("ToolB")
    assert f"<code>{tool_b} serve --port 9002</code>" in row("ToolB")
    assert "not found" in row("ToolC")
    assert "<code>tool-c --port 9003</code>" in row("ToolC")
    assert "<code>./run.sh</code>" in row("ToolD")


def test_open_a_venv_tool_starts_the_venv_executable(pypi_client, fake_runner, lookup_dirs):
    response = pypi_client.post("/open/ToolB", headers=SAME_ORIGIN, follow_redirects=False)

    assert response.headers["location"] == "http://127.0.0.1:9002/"
    (launch,) = fake_runner.started
    tool_b = str(lookup_dirs["venv_bin"] / "tool-b")
    assert launch.command() == (tool_b, "serve", "--port", "9002")
    assert launch.tool.workdir == lookup_dirs["home"]
