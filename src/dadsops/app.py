"""FastAPI app: home page, start/open, stop and settings."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from dadsops.config import THEMES, ConfigError, Settings, load_settings, save_settings
from dadsops.registry import RegistryError, Tool, load_registry
from dadsops.runner import HOST, Runner

PACKAGE_DIR = Path(__file__).resolve().parent


def create_app(registry_path: Path, config_path: Path, runner: Runner | None = None) -> FastAPI:
    runner = runner or Runner(log_dir=Path(config_path).parent / "logs")
    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        runner.stop_all()

    app = FastAPI(title="DadsOps", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
    app.state.runner = runner

    def current_settings() -> tuple[Settings, str | None]:
        try:
            return load_settings(config_path), None
        except ConfigError as exc:
            return Settings(), str(exc)

    def find_tool(name: str) -> Tool | None:
        """The named tool, or None if the registry is broken (the home page shows why)."""
        try:
            tools = load_registry(registry_path)
        except RegistryError:
            return None
        for tool in tools:
            if tool.name == name:
                return tool
        raise HTTPException(status_code=404, detail=f"unknown tool: {name}")

    def render(request: Request, template: str, status_code: int = 200, **context) -> Response:
        settings, settings_error = current_settings()
        context = {"theme": settings.theme, "settings_error": settings_error, **context}
        return templates.TemplateResponse(request, template, context, status_code=status_code)

    def home_redirect() -> RedirectResponse:
        return RedirectResponse("/", status_code=303)

    def require_same_origin(request: Request) -> None:
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            raise HTTPException(status_code=403, detail="cross-origin request refused")

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> Response:
        try:
            tools, registry_error = load_registry(registry_path), None
        except RegistryError as exc:
            tools, registry_error = [], str(exc)
        cards = [
            {
                "tool": tool,
                "running": tool.installed and runner.is_running(tool),
                "owned": runner.owns(tool.name),
                "error": runner.errors.get(tool.name),
            }
            for tool in tools
        ]
        return render(request, "home.html", cards=cards, registry_error=registry_error)

    @app.get("/open/{name}")
    def open_tool(name: str) -> RedirectResponse:
        tool = find_tool(name)
        if tool is None:
            return home_redirect()
        settings, settings_error = current_settings()
        if settings_error:
            runner.errors[name] = f"settings file is invalid: {settings_error}"
            return home_redirect()
        if not runner.start(tool, settings).ok:
            return home_redirect()
        return RedirectResponse(f"http://{HOST}:{tool.port}/", status_code=303)

    @app.post("/stop/{name}")
    def stop_tool(name: str, request: Request) -> RedirectResponse:
        require_same_origin(request)
        tool = find_tool(name)
        if tool is not None:
            runner.stop(tool)
        return home_redirect()

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, saved: bool = False) -> Response:
        settings, _ = current_settings()
        return render(
            request,
            "settings.html",
            settings=settings,
            themes=THEMES,
            config_path=config_path,
            saved=saved,
            form_error=None,
        )

    @app.post("/settings", response_class=HTMLResponse)
    async def save_settings_page(request: Request) -> Response:
        require_same_origin(request)
        form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        values = {key: form.get(key, [""])[0].strip() for key in ("aws_profile", "aws_region")}
        values["theme"] = form.get("theme", ["light"])[0]
        try:
            settings = Settings(**values)
        except ConfigError as exc:
            return render(
                request,
                "settings.html",
                status_code=400,
                settings=values,
                themes=THEMES,
                config_path=config_path,
                saved=False,
                form_error=str(exc),
            )
        save_settings(settings, config_path)
        return RedirectResponse("/settings?saved=1", status_code=303)

    return app
