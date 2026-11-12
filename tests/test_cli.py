import pytest

from dadsops import cli


@pytest.fixture
def served(monkeypatch) -> list[tuple[str, int]]:
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(cli, "is_source_checkout", lambda: False)
    monkeypatch.setattr(cli, "serve", lambda host, port, registry: calls.append((host, port)))
    return calls


def test_no_arguments_means_run_on_loopback_8080(served, monkeypatch):
    monkeypatch.delenv("DADSOPS_PORT", raising=False)
    assert cli.main([]) == 0
    assert served == [("127.0.0.1", 8080)]


def test_port_from_environment_and_flag(served, monkeypatch):
    monkeypatch.setenv("DADSOPS_PORT", "9100")
    cli.main(["run"])
    cli.main(["run", "--port", "9200"])
    assert served == [("127.0.0.1", 9100), ("127.0.0.1", 9200)]


@pytest.mark.parametrize("value", ["abc", "0", "70000"])
def test_bad_port_is_rejected(served, value):
    with pytest.raises(SystemExit):
        cli.main(["run", "--port", value])
    assert served == []
