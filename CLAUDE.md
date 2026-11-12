# DadsOps — Daddy's AWS Toolbox

Local-first launcher for the sibling AWS portfolio tools (EC2Patcher, IPLens,
TFLens, RoleLens). It discovers them, shows their status, starts the ones that
are not running and opens them in the browser. It is **read-only toward AWS**:
DadsOps itself never calls an AWS API, it only starts the other tools, which do
their own (also read-only) work.

## Rules for this repo

- Python 3.11+, `src/dadsops/` layout, FastAPI + Jinja2 templates, no build step.
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
src/dadsops/
  __init__.py
  app.py            # FastAPI app factory + routes
  registry.py       # tools.yaml loading + validation
  config.py         # shared settings file (AWS profile, region, theme)
  runner.py         # start/stop/status of a tool process (port + PID)
  cli.py            # `dadsops` command: install deps, run the toolbox
  templates/        # home page + card fragments
  static/           # css/js, no CDN
tools.yaml          # tool registry (name, path, start command, port)
tests/
  fixtures/         # small yaml/json fixtures
```
