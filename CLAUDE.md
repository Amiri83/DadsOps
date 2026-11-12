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

## Layout

```
dadsops/
  __init__.py       # __version__ (single source, read by pyproject.toml)
  __main__.py       # `python3 -m dadsops`
  cli.py            # `dadsops` command: bootstrap .venv, run the toolbox
  paths.py          # checkout vs installed, registry/config/log locations
  app.py            # FastAPI app factory + routes (home, open/stop, settings, logs)
  registry.py       # tools.yaml loading + validation + placeholders
  config.py         # shared settings (profiles, log dir, theme, per-app) + Launch
  forms.py          # settings page form <-> Settings
  logs.py           # safe per-tool log path + tail
  runner.py         # start/stop/status of a tool process (port + PID)
  default_tools.yaml  # packaged registry, copied to ~/.config/dadsops/ when installed
  templates/        # home, settings, logs pages + card fragment
  static/           # css/js, no CDN
tools.yaml          # tool registry for a checkout (name, path, start command, port)
.github/workflows/  # tests.yml (CI), publish.yml (PyPI trusted publishing on v* tags)
tests/
  fixtures/         # small yaml/json fixtures
```
