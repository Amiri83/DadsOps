"""Which response headers stop DadsOps from showing a tool in an iframe."""

import pytest

from dadsops.framing import Origin, check_framing, frame_blocker

DADSOPS = Origin.parse("http://127.0.0.1:8080/")
TOOL = Origin.parse("http://127.0.0.1:8083/")


def test_origin_parse_fills_in_the_default_port():
    assert Origin.parse("http://localhost/") == Origin("http", "localhost", 80)
    assert str(TOOL) == "http://127.0.0.1:8083"


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [("Content-Type", "text/html")],
        [("X-Frame-Options", "ALLOW-FROM http://127.0.0.1:8080")],  # obsolete, ignored
        [("Content-Security-Policy", "default-src 'self'")],
        [("Content-Security-Policy", "frame-ancestors *")],
        [("Content-Security-Policy", "frame-ancestors http:")],
        [("Content-Security-Policy", "frame-ancestors http://127.0.0.1:8080")],
        [("Content-Security-Policy", "frame-ancestors http://127.0.0.1:*")],
        [("Content-Security-Policy", "frame-ancestors 'self' 127.0.0.1:8080")],
        [("Content-Security-Policy-Report-Only", "frame-ancestors 'none'")],
        [("X-Frame-Options", "DENY"), ("Content-Security-Policy", "frame-ancestors *")],
    ],
)
def test_permissive_headers_allow_framing(headers):
    assert frame_blocker(headers, DADSOPS, TOOL) is None


@pytest.mark.parametrize(
    ("headers", "blocked_by"),
    [
        ([("X-Frame-Options", "DENY")], "X-Frame-Options: DENY"),
        ([("x-frame-options", " deny ")], "X-Frame-Options: DENY"),
        ([("X-Frame-Options", "SAMEORIGIN")], "X-Frame-Options: SAMEORIGIN"),
        ([("X-Frame-Options", "SAMEORIGIN, DENY")], "X-Frame-Options: DENY"),
        (
            [("Content-Security-Policy", "frame-ancestors 'none'")],
            "Content-Security-Policy: frame-ancestors 'none'",
        ),
        (
            [("Content-Security-Policy", "script-src 'self'; frame-ancestors 'self'")],
            "Content-Security-Policy: frame-ancestors 'self'",
        ),
        (
            [("Content-Security-Policy", "frame-ancestors http://127.0.0.1")],
            "Content-Security-Policy: frame-ancestors http://127.0.0.1",
        ),
        (
            [("Content-Security-Policy", "frame-ancestors https://192.0.2.1:8080")],
            "Content-Security-Policy: frame-ancestors https://192.0.2.1:8080",
        ),
        (
            [("Content-Security-Policy", "frame-ancestors")],
            "Content-Security-Policy: frame-ancestors",
        ),
        (  # every enforced policy must allow it
            [
                ("Content-Security-Policy", "frame-ancestors *"),
                ("Content-Security-Policy", "frame-ancestors 'self'"),
            ],
            "Content-Security-Policy: frame-ancestors 'self'",
        ),
    ],
)
def test_blocking_headers_are_named(headers, blocked_by):
    assert frame_blocker(headers, DADSOPS, TOOL) == blocked_by


def test_same_origin_rules_pass_when_origins_match():
    headers = [("X-Frame-Options", "SAMEORIGIN")]
    assert frame_blocker(headers, TOOL, TOOL) is None
    assert (
        frame_blocker([("Content-Security-Policy", "frame-ancestors 'self'")], TOOL, TOOL) is None
    )


def test_check_framing_against_a_fake_server(fake_tool_server):
    blocking = fake_tool_server([("X-Frame-Options", "DENY")])
    permissive = fake_tool_server()

    blocked = check_framing(f"http://127.0.0.1:{blocking}/", "http://127.0.0.1:8080/")
    allowed = check_framing(f"http://127.0.0.1:{permissive}/", "http://127.0.0.1:8080/")

    assert (blocked.allowed, blocked.blocked_by, blocked.error) == (
        False,
        "X-Frame-Options: DENY",
        None,
    )
    assert (allowed.allowed, allowed.blocked_by, allowed.error) == (True, None, None)


def test_a_redirect_off_the_tool_is_not_followed(fake_tool_server):
    port = fake_tool_server(redirect_to="http://192.0.2.1/elsewhere")

    result = check_framing(f"http://127.0.0.1:{port}/", "http://127.0.0.1:8080/")

    assert result.allowed and result.error is None
