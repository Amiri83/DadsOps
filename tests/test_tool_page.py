"""The tool page: a tool opened inside DadsOps (top bar + iframe), or why it cannot be."""

import re
import shutil
import socket

import pytest
from fastapi.testclient import TestClient
from markupsafe import escape  # what the templates escape with

from dadsops.app import create_app
from dadsops.config import AppSettings, Settings, save_settings
from dadsops.framing import FrameCheck
from dadsops.paths import DEFAULT_REGISTRY
from dadsops.runner import Runner, StartResult

SAME_ORIGIN = {"origin": "http://testserver"}
ALLOW_ALL = FrameCheck(allowed=True)


class FakeRunner(Runner):
    """Records starts instead of spawning; the tool counts as running once started."""

    def __init__(self, start_result: StartResult | None = None):
        super().__init__(probe=lambda tool: tool.name in self.running)
        self.start_result = start_result or StartResult(ok=True, started=True, message="ok")
        self.started: list = []
        self.running: set[str] = set()

    def start(self, launch):
        self.started.append(launch)
        if self.start_result.ok:
            self.running.add(launch.tool.name)
            self.errors.pop(launch.tool.name, None)
        else:
            self.errors[launch.tool.name] = self.start_result.message
        return self.start_result


@pytest.fixture
def fake_runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def config_file(tmp_path):
    return tmp_path / "config" / "config.yaml"


@pytest.fixture
def default_registry(tmp_path, lookup_dirs, make_executable):
    """The packaged registry, with every bundled console script in the DadsOps venv bin/."""
    for program in ("ec2patcher", "iplens", "tflens"):
        make_executable(lookup_dirs["venv_bin"], program)
    path = tmp_path / "tools.yaml"
    shutil.copy(DEFAULT_REGISTRY, path)
    return path


def make_client(registry, config_file, runner, frame_check=None):
    kwargs = {} if frame_check is None else {"frame_check": frame_check}
    return TestClient(create_app(registry, config_file, runner=runner, **kwargs))


def iframe_src(page: str) -> str:
    match = re.search(r'<iframe class="tool-frame" src="([^"]+)"', page)
    assert match, "no tool iframe on the page"
    return match.group(1)


def top_bar(page: str) -> str:
    return page[page.index('<header class="shell-header">') : page.index("</header>")]


# ---------------------------------------------------------------- iframe and ports


@pytest.mark.parametrize(
    ("name", "port"), [("EC2Patcher", 8081), ("IPLens", 8082), ("TFLens", 8083)]
)
def test_tool_page_frames_the_tool_at_its_default_port(
    default_registry, config_file, fake_runner, name, port
):
    seen = []

    def frame_check(url, embedder):
        seen.append((url, embedder))
        return ALLOW_ALL

    with make_client(default_registry, config_file, fake_runner, frame_check) as client:
        page = client.get(f"/tool/{name}")

    assert page.status_code == 200
    assert iframe_src(page.text) == f"http://127.0.0.1:{port}/"
    assert seen == [(f"http://127.0.0.1:{port}/", "http://testserver/")]
    assert [launch.tool.name for launch in fake_runner.started] == [name]
    bar = top_bar(page.text)
    for href in ('href="/"', 'href="/settings"', 'href="/logs"'):
        assert href in bar
    assert '<span class="switcher" role="group" aria-label="Tools">' in bar
    assert f'href="/tool/{name}" aria-current="page"' in bar
    assert 'class="tool-view"' in page.text, "full-height layout, no page scrolling"
    assert "shell-footer" not in page.text


def test_tool_page_uses_the_port_overridden_in_settings(default_registry, config_file, fake_runner):
    save_settings(Settings(apps={"TFLens": AppSettings(port=9183)}), config_file)

    with make_client(default_registry, config_file, fake_runner, lambda *_: ALLOW_ALL) as client:
        page = client.get("/tool/TFLens")

    assert iframe_src(page.text) == "http://127.0.0.1:9183/"
    (launch,) = fake_runner.started
    assert launch.tool.port == 9183
    assert launch.command()[-2:] == ("--port", "9183")


def test_switcher_lists_openable_tools_only(default_registry, config_file, fake_runner):
    save_settings(Settings(apps={"IPLens": AppSettings(enabled=False)}), config_file)

    with make_client(default_registry, config_file, fake_runner) as client:
        bar = top_bar(client.get("/").text)

    assert re.findall(r'href="/tool/(\w+)"', bar) == ["EC2Patcher", "TFLens"]


def test_card_open_goes_to_the_tool_page(default_registry, config_file, fake_runner):
    with make_client(default_registry, config_file, fake_runner, lambda *_: ALLOW_ALL) as client:
        opened = client.post("/open/IPLens", headers=SAME_ORIGIN, follow_redirects=False)
        page = client.get(opened.headers["location"])

    assert opened.status_code == 303
    assert opened.headers["location"] == "/tool/IPLens"
    assert iframe_src(page.text) == "http://127.0.0.1:8082/"
    assert len(fake_runner.started) == 1, "already running: the tool page does not restart it"


# ---------------------------------------------------------------- framing check, real server


def tool_registry(write_registry, make_executable, lookup_dirs, ports: dict[str, int]):
    entries = []
    for name, port in ports.items():
        make_executable(lookup_dirs["venv_bin"], name)
        entries.append({"name": name, "start_command": f"{name} --port {{port}}", "port": port})
    return write_registry(entries)


@pytest.fixture
def blocking_and_permissive(fake_tool_server, write_registry, make_executable, lookup_dirs):
    ports = {
        "tool_a": fake_tool_server([("X-Frame-Options", "DENY")]),
        "tool_b": fake_tool_server([("Content-Security-Policy", "default-src 'self'")]),
    }
    return tool_registry(write_registry, make_executable, lookup_dirs, ports), ports


def test_blocking_tool_reports_the_header_and_offers_same_tab(
    blocking_and_permissive, config_file, fake_runner
):
    registry, ports = blocking_and_permissive
    with make_client(registry, config_file, fake_runner) as client:
        page = client.get("/tool/tool_a")

    assert page.status_code == 200
    assert "<iframe" not in page.text
    assert "tool_a cannot be shown inside DadsOps" in page.text
    assert '<code class="blocked-by">X-Frame-Options: DENY</code>' in page.text
    url = f"http://127.0.0.1:{ports['tool_a']}/"
    assert f'<a class="btn same-tab" href="{url}" target="_self">Open tool_a in this tab</a>' in (
        page.text
    )
    assert '<header class="shell-header">' in page.text


def test_permissive_tool_is_framed(blocking_and_permissive, config_file, fake_runner):
    registry, ports = blocking_and_permissive
    with make_client(registry, config_file, fake_runner) as client:
        page = client.get("/tool/tool_b")

    assert page.status_code == 200
    assert iframe_src(page.text) == f"http://127.0.0.1:{ports['tool_b']}/"
    assert "cannot be shown" not in page.text


@pytest.mark.parametrize(
    ("headers", "blocked_by"),
    [
        ([("X-Frame-Options", "sameorigin")], "X-Frame-Options: SAMEORIGIN"),
        (
            [("Content-Security-Policy", "default-src 'self'; frame-ancestors 'self'")],
            "Content-Security-Policy: frame-ancestors 'self'",
        ),
        (
            [("Content-Security-Policy", "frame-ancestors 'none'")],
            "Content-Security-Policy: frame-ancestors 'none'",
        ),
    ],
)
def test_other_blocking_headers_are_named(
    fake_tool_server, write_registry, make_executable, lookup_dirs, config_file, fake_runner,
    headers, blocked_by,
):  # fmt: skip
    port = fake_tool_server(headers)
    registry = tool_registry(write_registry, make_executable, lookup_dirs, {"tool_a": port})
    with make_client(registry, config_file, fake_runner) as client:
        page = client.get("/tool/tool_a")

    assert f'<code class="blocked-by">{escape(blocked_by)}</code>' in page.text
    assert "<iframe" not in page.text


def test_frame_ancestors_listing_dadsops_wins_over_x_frame_options(
    fake_tool_server, write_registry, make_executable, lookup_dirs, config_file, fake_runner
):
    headers = [
        ("X-Frame-Options", "DENY"),
        ("Content-Security-Policy", "frame-ancestors http://testserver"),
    ]
    port = fake_tool_server(headers)
    registry = tool_registry(write_registry, make_executable, lookup_dirs, {"tool_a": port})
    with make_client(registry, config_file, fake_runner) as client:
        page = client.get("/tool/tool_a")

    assert iframe_src(page.text) == f"http://127.0.0.1:{port}/"


def test_headers_are_read_after_a_same_origin_redirect(
    fake_tool_server, write_registry, make_executable, lookup_dirs, config_file, fake_runner
):
    port = fake_tool_server([("X-Frame-Options", "DENY")], redirect_to="/app")
    registry = tool_registry(write_registry, make_executable, lookup_dirs, {"tool_a": port})
    with make_client(registry, config_file, fake_runner) as client:
        page = client.get("/tool/tool_a")

    assert "X-Frame-Options: DENY" in page.text


# ---------------------------------------------------------------- errors instead of a blank frame


def test_start_failure_shows_the_start_error(default_registry, config_file):
    runner = FakeRunner(StartResult(ok=False, started=False, message="command exited with code 3"))
    with make_client(default_registry, config_file, runner, lambda *_: ALLOW_ALL) as client:
        page = client.get("/tool/IPLens")
        home = client.get("/")

    assert page.status_code == 502
    assert "<iframe" not in page.text
    assert "IPLens could not be opened: command exited with code 3" in page.text
    assert 'href="/logs/IPLens"' in page.text
    assert '<p class="failure" role="alert">command exited with code 3</p>' in home.text


def test_unreachable_tool_shows_an_error(
    write_registry, make_executable, lookup_dirs, config_file, fake_runner
):
    """Reported as started, but nothing answers HTTP on its port."""
    with socket.socket() as probe:  # bind to port 0 and close: a port nobody listens on
        probe.bind(("127.0.0.1", 0))
        dead = probe.getsockname()[1]
    registry = tool_registry(write_registry, make_executable, lookup_dirs, {"tool_a": dead})
    with make_client(registry, config_file, fake_runner) as client:
        page = client.get("/tool/tool_a")

    assert page.status_code == 502
    assert "<iframe" not in page.text
    assert f"tool_a could not be opened: cannot reach http://127.0.0.1:{dead}/" in page.text


def test_missing_tool_page_shows_why(tmp_path, lookup_dirs, config_file):
    registry = tmp_path / "tools.yaml"
    shutil.copy(DEFAULT_REGISTRY, registry)
    with make_client(registry, config_file, Runner(probe=lambda _tool: False)) as client:
        page = client.get("/tool/IPLens")

    assert page.status_code == 502
    assert escape("'iplens' not found in the DadsOps venv or on PATH") in page.text
    assert "pip install aws-iplens" in page.text


def test_unknown_tool_page_is_404(default_registry, config_file, fake_runner):
    with make_client(default_registry, config_file, fake_runner) as client:
        assert client.get("/tool/nope").status_code == 404


def test_cross_site_link_asks_before_starting(default_registry, config_file, fake_runner):
    with make_client(default_registry, config_file, fake_runner, lambda *_: ALLOW_ALL) as client:
        page = client.get("/tool/TFLens", headers={"sec-fetch-site": "cross-site"})

    assert fake_runner.started == []
    assert "<iframe" not in page.text
    assert '<form method="post" action="/open/TFLens">' in page.text
