# DadsOps — Daddy's AWS Toolbox

Local-first launcher for the sibling AWS portfolio tools (EC2Patcher, IPLens,
TFLens, RoleLens). It discovers them, shows their status, starts the ones that
are not running and opens them in the browser. It is **read-only toward AWS**:
DadsOps itself never calls an AWS API, it only starts the other tools, which do
their own (also read-only) work.

## Rules for this repo

- Python 3.11+, flat `dadsops/` layout, FastAPI + Jinja2 templates, no build step.
- `__init__.py`, `__main__.py`, `cli.py` and `paths.py` import only the standard library
  so a bare `python3 -m dadsops` can bootstrap `.venv`.
- `pyproject.toml` is the single source of truth for deps and the `dadsops` entry point.
- **No network in tests.** Bind to `127.0.0.1` on port 0, never to a real port; start a
  real process only through a fake command, a stub script in `tmp_path` or a monkeypatched
  runner.
- No real account IDs, ARNs, hostnames, IPs, bucket names or profile names. Use
  placeholders: `ip-10-0-0-1`, `192.0.2.1`, `i-0123456789abcdef0`, `123456789012`,
  `example`, `amzn-s3-demo-bucket`.
- Never pass secrets through a command line, log line or config file.
- Tests: pytest, generic fixture names (`tool_a`, `tool_b`, `fake_tool`), `tmp_path` for
  every registry/config file.
- Lint gate: `ruff check .` and `ruff format --check .` must pass; keep the config in
  `pyproject.toml`.
- Update `README.md` when behaviour or the registry schema changes.
- Registry `path` is optional: without it a tool is a console script found in the DadsOps
  venv `bin/` (first) or on PATH and runs in `~`. The bundled tools are `pyproject.toml`
  dependencies; `package:` names the distribution for the `pip install` hint. Tests isolate PATH, `registry.VENV_BIN` and
  HOME with the `lookup_dirs` fixture; never rely on what is installed on the machine.
- One dark theme only. `dadsops/static/theme/dadsops-theme.css` is the master copy shared
  with the other tools: plain CSS, no CDN/imports/image URLs, first line is its version
  comment (bump it on any change). App-specific CSS goes in `static/style.css`, linked after
  the theme. `reference/` is gitignored stage-copy input: never edit or commit it.

## Layout

```
dadsops/
  __init__.py       # __version__ (single source, read by pyproject.toml)
  __main__.py       # `python3 -m dadsops`
  cli.py            # `dadsops` command: bootstrap .venv, run the toolbox
  paths.py          # checkout vs installed, registry/config/log locations
  app.py            # FastAPI app factory + routes (home, open/stop, settings, logs, errors)
  registry.py       # tools.yaml loading + validation + placeholders + Location (where found)
  config.py         # shared settings (profiles, log dir, per-app) + Launch
  forms.py          # settings page form <-> Settings
  logs.py           # safe per-tool log path + tail
  runner.py         # start/stop/status of a tool process (port + PID)
  framing.py        # probe a tool's X-Frame-Options / CSP frame-ancestors for the tool page
  default_tools.yaml  # packaged registry (console scripts, no paths), copied to ~/.config/dadsops/
  templates/        # base (top menu + tool switcher), home, tool (iframe), settings, logs, error + card
  static/
    theme/dadsops-theme.css  # shared theme, master copy (tools copy it unchanged)
    style.css       # DadsOps-only layout, after the theme
    app.js          # "Starting…" feedback; no CDN
tools.yaml          # tool registry for a checkout (same console scripts as the default, no paths)
reference/          # gitignored TFLens CSS copies, read-only input
.github/workflows/  # tests.yml (CI), publish.yml (PyPI trusted publishing on v* tags)
tests/
  fixtures/         # small yaml/json fixtures
```
