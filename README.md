# DadsOps — Daddy's AWS Toolbox

A small local web launcher for the sibling AWS portfolio tools (EC2Patcher,
IPLens, TFLens, RoleLens). It shows one card per tool with a live status badge.
Click a card (a plain form POST, no JavaScript needed) and DadsOps starts that
tool if it is not running yet, waits for its port to answer, then opens it in
the same browser tab.

DadsOps is **read-only toward AWS**. It never calls an AWS API and never stores
credentials. It only passes profile and region *names* to the tools it starts,
and those tools do their own (also read-only) work.

## Quick start (from a checkout)

```bash
git clone <this repo> DadsOps && cd DadsOps
python3 -m dadsops            # serve on http://127.0.0.1:8080/
```

No `PYTHONPATH` and no activated venv needed. The current interpreter only runs
the bootstrap: it creates `.venv` if it is missing, installs the project into it
(`pip install -e .`) if anything is missing, then re-executes itself inside
`.venv` and serves. Later runs reuse the venv and start straight away.

- `python3 -m dadsops` with no arguments is the same as `python3 -m dadsops run`;
  `python3 -m dadsops install` only does the venv part.
- `--port` or `DADSOPS_PORT` sets the port (default `8080`). `--host` sets the
  bind address (default `127.0.0.1`; anything else prints a warning).
- `--registry` or `DADSOPS_REGISTRY` points to another registry file.

## Install with pip

```bash
pip install dadsops
dadsops                        # same as `dadsops run`
```

On first run the packaged default registry is copied to
`~/.config/dadsops/tools.yaml`. Edit that copy to point the tools at your
checkouts. It is never overwritten, not even by an upgrade.

Which registry is used, first match wins:

1. `--registry <file>`
2. `$DADSOPS_REGISTRY`
3. `tools.yaml` in the repo, when running from a checkout
4. `~/.config/dadsops/tools.yaml` (created from the packaged default if missing)

## Tool registry (`tools.yaml`)

```yaml
tools:
  - name: EC2Patcher              # required; letters, digits, - and _ (used in URLs and log names)
    path: ../EC2Patcher           # required; relative to tools.yaml, ~ allowed
    start_command: .venv/bin/python -m ec2patcher --port {port}   # required; string or list
    port: 8081                    # required; 1-65535, unique
    description: Patch EC2 servers from CVE lists   # optional
    health_path: /healthz         # optional; if set, "ready" = HTTP GET answers non-5xx
    coming_soon: false            # optional; shows the "coming soon" badge, never started
```

`start_command` runs in `path` as an argument list, never through a shell. A
string is split like a shell would (`shlex.split`); a list is used as is.

### Placeholders

Every placeholder in any argument is replaced when the tool is started:

| Placeholder | Replaced with                                                       |
|-------------|---------------------------------------------------------------------|
| `{port}`    | the tool's port (per-app setting, else `port` from the registry)    |
| `{profile}` | the AWS profile name picked for the tool (else the global default)  |
| `{region}`  | that profile's region                                               |
| `{log_dir}` | the global log directory, `~` expanded                              |

So `--port {port}` becomes `--port 8081` and `--listen=127.0.0.1:{port}`
becomes `--listen=127.0.0.1:8081`. Any other `{word}` is an **unknown
placeholder and a registry error** at load time. Braces that do not wrap a
single word (`{}`, `{"a": 1}`) are left alone. A command that uses `{profile}`
or `{region}` while no AWS profile is configured is refused with that reason.

Validation also rejects duplicate names or ports, missing required keys,
unknown keys, a bad port and an empty command. If a tool's `path` does not
exist, that is not an error: the tool just shows **not found**. If the registry
can't be read or is invalid, every page shows the error instead of crashing.

## Settings (one place for all apps)

Stored in `~/.config/dadsops/config.yaml` (override with `DADSOPS_CONFIG`) and
edited from the **Settings** page:

```yaml
theme: light                              # light | dark
log_dir: ~/.local/state/dadsops/logs      # the default
profiles:                                 # exactly one marked default (if any are listed)
  - name: example
    region: us-east-1
    default: true
  - name: example-dev
    region: eu-west-1
apps:                                     # only what differs from the default
  EC2Patcher:
    port: 9081                            # instead of the registry port
    profile: example-dev                  # instead of the default profile
    enabled: false                        # hide the start button
    extra_args: [--verbose]               # appended to start_command
```

**Precedence:** a per-app setting beats `tools.yaml`; anything not set falls
back to `tools.yaml` (port) or the global default (profile). Disabled apps show
a **disabled** badge and are not started.

On the page you can add a profile (fill in the blank last row), remove one
(tick *Remove*), pick the default (radio button) and edit every app row. Extra
args are typed like a shell line (`--name 'a b'` gives two arguments).

The file is written atomically (temp file in the same directory, fsync, rename)
with mode `0600`. A bad submit shows the reason and writes nothing. It is
rejected when:

- a port is outside 1–65535, or two apps would end up on the same port
  (registry ports count too);
- a picked profile is not in the profile list, or there is not exactly one
  default profile;
- a profile name or region is malformed (`us-east-1` style regions);
- extra args can't be split, or one looks like a secret (contains `secret`,
  `token`, `passw`, `access_key` or an AWS access key ID).

Unknown keys are rejected too, so the file cannot hold credentials: only
profile names, regions, ports, paths and plain arguments.

### What each tool receives

Environment variables, on top of DadsOps' own environment:

| Variable          | Value                                             |
|-------------------|---------------------------------------------------|
| `PORT`            | the tool's port                                   |
| `AWS_PROFILE`     | the picked (or default) profile name, if any      |
| `AWS_REGION`      | that profile's region, if any                     |
| `DADSOPS_LOG_DIR` | the log directory                                 |
| `DADSOPS_THEME`   | `light` or `dark`                                 |

## Logs

Each tool's stdout and stderr go to `<log_dir>/<tool>.log`, truncated each time
DadsOps starts it. The **Logs** page (`/logs`) lists every tool in the registry
with the last 200 lines of its log (or "No output yet"); `/logs/<tool>` shows
one tool. A name that is not a plain registry name (anything with `.`, `/`,
`\` and so on) is refused with `400`, so a request can never read a file
outside the log directory.

## Starting and stopping

- `POST /open/<tool>` (the card's form) starts the tool if needed and redirects to
  `http://127.0.0.1:<port>/`. If the port already answers, or DadsOps already
  holds a live process for the tool, it only redirects. A start waits up to 30s
  for the port. If it times out, the process is stopped and the card shows why.
  Exit codes and the last output line of a failed command show on the card too.
- `POST /stop/<tool>` stops the tool only if DadsOps started it in this session
  (its process group gets SIGTERM, then SIGKILL after 5s). A process DadsOps did
  not start is left alone, even if it holds the tool's port. When DadsOps shuts
  down, it stops every tool it started.
- Every `POST` (`/open`, `/stop`, `/settings`) must come from the DadsOps page
  itself: its `Origin` header (or `Referer`, if there is no `Origin`) has to
  match the server's host. Otherwise it gets `403`, and so does a request with
  neither header. `GET /open/<tool>` does not exist (`405`), so a link or an
  embedded image elsewhere cannot start a tool.

## Development

```bash
python3 -m dadsops install            # create .venv and install the project
.venv/bin/pip install -e ".[dev]"     # pytest, httpx, ruff
.venv/bin/python -m pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

Tests make no network calls and start no real tools. Process tests use a stub
script in `tmp_path`, and readiness is checked through a marker file instead of
a socket.

## Releasing

CI (`.github/workflows/tests.yml`) runs lint and tests on Python 3.11–3.13 and
builds the wheel. To publish, bump `__version__` in `dadsops/__init__.py`, then
push a matching tag (`v0.1.0`). `.github/workflows/publish.yml` checks the tag
against the version, runs the checks, builds, and uploads to PyPI with Trusted
Publishing (OIDC, GitHub environment `pypi`). No token is stored anywhere:
register the trusted publisher on PyPI for this repository, workflow
`publish.yml`, environment `pypi`.
