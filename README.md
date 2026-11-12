# DadsOps — Daddy's AWS Toolbox

A small local web launcher for the sibling AWS portfolio tools (EC2Patcher,
IPLens, TFLens, RoleLens). It shows one card per tool with a live status badge.
Click a card and DadsOps starts that tool if it is not running yet, waits for its
port to answer, then opens it in the same browser tab.

DadsOps is **read-only toward AWS**. It never calls an AWS API and never stores
credentials. It only passes the shared profile and region *names* to the tools
it starts, and those tools do their own (also read-only) work.

## Install and run

```bash
PYTHONPATH=src python3 -m dadsops install   # first time: create/reuse .venv, install missing deps
.venv/bin/dadsops run                       # serve on http://127.0.0.1:8080/
```

- `dadsops` with no arguments is the same as `dadsops run`.
- `dadsops run` first does what `install` does (creates or reuses `.venv`,
  pip-installs the project if anything is missing), re-executes itself inside
  that venv, then serves.
- `--port` or `DADSOPS_PORT` sets the port (default `8080`). `--host` sets the
  bind address (default `127.0.0.1`; anything else prints a warning).
- `--registry` or `DADSOPS_REGISTRY` points to another registry file (default
  `tools.yaml` in the repo).
- `python -m dadsops ...` works the same way as the console script.

## Tool registry (`tools.yaml`)

```yaml
tools:
  - name: EC2Patcher              # required; letters, digits, - and _ (used in URLs)
    path: ../EC2Patcher           # required; relative to tools.yaml, ~ allowed
    start_command: .venv/bin/python -m ec2patcher   # required; string or list, run in `path`
    port: 8081                    # required; 1-65535, unique
    description: Patch status...  # optional
    health_path: /healthz         # optional; if set, "ready" = HTTP GET answers non-5xx
    coming_soon: false            # optional; shows the "coming soon" badge, never started
```

Validation rejects duplicate names or ports, missing required keys, unknown
keys, a bad port and an empty command. If a tool's `path` does not exist, that
is not an error: the tool just shows **not found**. If the registry file can't
be read or is invalid, the home page shows the error instead of crashing.

Status badges come from a live check on every page load: `coming soon` if
flagged, otherwise `installed` when `path` exists, otherwise `not found`. A
`running on :port` badge appears when the tool's port (or health path) answers.

## Starting and stopping

- `GET /open/<tool>` starts the tool if needed and redirects to
  `http://127.0.0.1:<port>/`. If the port already answers, or DadsOps already
  holds a live process for the tool, it only redirects. A start waits up to 30s
  for the port. If it times out, the process is stopped and the card shows why.
  Exit codes and the last output line of a failed command show on the card too.
- Tool output goes to `<config dir>/logs/<tool>.log`.
- `POST /stop/<tool>` stops the tool only if DadsOps started it in this session
  (its process group gets SIGTERM, then SIGKILL after 5s). A process DadsOps did
  not start is left alone, even if it holds the tool's port. When DadsOps shuts
  down, it stops every tool it started.

## Shared settings

Stored in `~/.config/dadsops/config.yaml` (override with `DADSOPS_CONFIG`),
outside the repo. You can edit it from the **Settings** page:

```yaml
aws_profile: example
aws_region: us-east-1
theme: light            # light | dark
```

The file is written atomically (temp file in the same directory, fsync, rename)
with mode `0600`. Unknown keys are rejected, so it cannot hold credentials.
Every started tool gets these environment variables: `AWS_PROFILE`,
`AWS_REGION`, `DADSOPS_THEME` and `PORT`. Empty profile or region values are
not forwarded.

## Development

```bash
.venv/bin/python -m pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

Tests make no network calls and start no real tools. Process tests use a stub
script in `tmp_path`, and readiness is checked through a marker file instead of
a socket.
