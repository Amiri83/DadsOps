"""FastAPI app: home page, start/open, stop, settings and logs."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from dadsops.config import (
    THEMES,
    ConfigError,
    Launch,
    Settings,
    load_settings,
    resolve,
    save_settings,
)
from dadsops.forms import form_values, read_form, settings_from_form
from dadsops.logs import TAIL_LINES, log_file, tail
from dadsops.paths import PACKAGE_DIR
from dadsops.registry import RegistryError, Tool, load_registry
from dadsops.runner import HOST, Runner


@dataclass(frozen=True)
class State:
    """Registry and settings as read for one request, with what went wrong reading them."""

    tools: list[Tool]
    registry_error: str | None
    settings: Settings
    settings_error: str | None
    launches: dict[str, Launch]


def create_app(registry_path: Path, config_path: Path, runner: Runner | None = None) -> FastAPI:
    runner = runner or Runner()
    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        runner.stop_all()

    app = FastAPI(title="DadsOps", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
    app.state.runner = runner

    def load_state() -> State:
        try:
            tools, registry_error = load_registry(registry_path), None
        except RegistryError as exc:
            tools, registry_error = [], str(exc)
        try:
            settings, settings_error = load_settings(config_path), None
        except ConfigError as exc:
            settings, settings_error = Settings(), str(exc)
        try:
            launches = by_name(tools, settings)
        except ConfigError as exc:
            # Saved settings clash with a changed registry: show why, keep them editable.
            settings_error, launches = str(exc), by_name(tools, Settings())
        return State(tools, registry_error, settings, settings_error, launches)

    def by_name(tools: list[Tool], settings: Settings) -> dict[str, Launch]:
        return {launch.tool.name: launch for launch in resolve(tools, settings)}

    def find_launch(state: State, name: str) -> Launch | None:
        """The named tool, or None if the registry is broken (the home page shows why)."""
        if state.registry_error:
            return None
        if name not in state.launches:
            raise HTTPException(status_code=404, detail=f"unknown tool: {name}")
        return state.launches[name]

    def render(
        request: Request, template: str, state: State, status_code: int = 200, **context
    ) -> Response:
        context = {
            "theme": state.settings.theme,
            "settings_error": state.settings_error,
            "registry_error": state.registry_error,
            **context,
        }
        return templates.TemplateResponse(request, template, context, status_code=status_code)

    def home_redirect() -> RedirectResponse:
        return RedirectResponse("/", status_code=303)

    def require_same_origin(request: Request) -> None:
        """Refuse a POST unless its Origin (or, failing that, Referer) is this server."""
        source = request.headers.get("origin") or request.headers.get("referer")
        if not source or urlsplit(source).netloc != request.headers.get("host"):
            raise HTTPException(status_code=403, detail="cross-origin request refused")

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> Response:
        state = load_state()
        cards = []
        for launch in state.launches.values():
            tool = launch.tool
            status = tool.status
            if status == "installed" and not launch.enabled:
                status = "disabled"
            cards.append(
                {
                    "tool": tool,
                    "status": status,
                    "running": tool.installed and runner.is_running(tool),
                    "owned": runner.owns(tool.name),
                    "error": runner.errors.get(tool.name),
                }
            )
        return render(request, "home.html", state, cards=cards)

    @app.post("/open/{name}")
    def open_tool(name: str, request: Request) -> RedirectResponse:
        require_same_origin(request)
        state = load_state()
        launch = find_launch(state, name)
        if launch is None:
            return home_redirect()
        if state.settings_error:
            runner.errors[name] = f"settings are invalid: {state.settings_error}"
            return home_redirect()
        if not runner.start(launch).ok:
            return home_redirect()
        return RedirectResponse(f"http://{HOST}:{launch.tool.port}/", status_code=303)

    @app.post("/stop/{name}")
    def stop_tool(name: str, request: Request) -> RedirectResponse:
        require_same_origin(request)
        launch = find_launch(load_state(), name)
        if launch is not None:
            runner.stop(launch.tool)
        return home_redirect()

    def settings_response(
        request: Request,
        state: State,
        values: dict,
        form_error: str | None = None,
        saved: bool = False,
    ) -> Response:
        return render(
            request,
            "settings.html",
            state,
            status_code=400 if form_error else 200,
            form=values,
            themes=THEMES,
            config_path=config_path,
            saved=saved,
            form_error=form_error,
        )

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, saved: bool = False) -> Response:
        state = load_state()
        values = form_values(state.settings, state.tools)
        return settings_response(request, state, values, saved=saved)

    @app.post("/settings", response_class=HTMLResponse)
    async def save_settings_page(request: Request) -> Response:
        require_same_origin(request)
        state = load_state()
        form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        values = read_form(form, state.tools)
        try:
            settings = settings_from_form(values, state.settings)
            resolve(state.tools, settings)
        except ConfigError as exc:
            return settings_response(request, state, values, form_error=str(exc))
        save_settings(settings, config_path)
        return RedirectResponse("/settings?saved=1", status_code=303)

    def log_entry(launch: Launch) -> dict:
        return {"launch": launch, "path": launch.log_path, "lines": tail(launch.log_path)}

    @app.get("/logs", response_class=HTMLResponse)
    def logs_page(request: Request) -> Response:
        state = load_state()
        entries = [log_entry(launch) for launch in state.launches.values()]
        return render(request, "logs.html", state, entries=entries, tail_lines=TAIL_LINES)

    @app.get("/logs/{name}", response_class=HTMLResponse)
    def tool_log_page(name: str, request: Request) -> Response:
        state = load_state()
        try:
            log_file(Path(state.settings.log_dir).expanduser(), name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        if name not in state.launches:
            raise HTTPException(status_code=404, detail=f"unknown tool: {name}")
        entries = [log_entry(state.launches[name])]
        return render(request, "logs.html", state, entries=entries, tail_lines=TAIL_LINES)

    return app
