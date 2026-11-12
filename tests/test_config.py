import os
from pathlib import Path

import pytest

from dadsops import config as config_module
from dadsops.config import (
    ConfigError,
    Settings,
    child_env,
    config_path,
    load_settings,
    save_settings,
)


def test_missing_file_gives_defaults(tmp_path: Path):
    assert load_settings(tmp_path / "config.yaml") == Settings()


def test_save_then_load_round_trip(tmp_path: Path):
    path = tmp_path / "sub" / "config.yaml"
    settings = Settings(aws_profile="example", aws_region="eu-west-1", theme="dark")

    save_settings(settings, path)

    assert load_settings(path) == settings
    assert (path.stat().st_mode & 0o777) == 0o600
    assert [p.name for p in path.parent.iterdir()] == ["config.yaml"], "no temp files left"


def test_save_is_atomic_when_replace_fails(tmp_path: Path, monkeypatch):
    path = tmp_path / "config.yaml"
    save_settings(Settings(aws_profile="example"), path)
    before = path.read_text()

    def broken_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(config_module.os, "replace", broken_replace)
    with pytest.raises(OSError, match="disk full"):
        save_settings(Settings(aws_profile="other", theme="dark"), path)

    assert path.read_text() == before
    assert [p.name for p in tmp_path.iterdir()] == ["config.yaml"]


def test_child_env_forwards_profile_region_theme_and_port():
    settings = Settings(aws_profile="example", aws_region="us-west-2", theme="dark")

    env = child_env(settings, 9001, base={"PATH": "/usr/bin"})

    assert env == {
        "PATH": "/usr/bin",
        "PORT": "9001",
        "AWS_PROFILE": "example",
        "AWS_REGION": "us-west-2",
        "DADSOPS_THEME": "dark",
    }


def test_child_env_skips_empty_names():
    env = child_env(Settings(aws_profile="", aws_region=""), 9001, base={})
    assert "AWS_PROFILE" not in env and "AWS_REGION" not in env


@pytest.mark.parametrize(
    "kwargs",
    [{"aws_profile": "bad profile"}, {"aws_region": "Mars"}, {"theme": "neon"}],
)
def test_invalid_values_rejected(kwargs):
    with pytest.raises(ConfigError):
        Settings(**kwargs)


def test_unknown_keys_rejected(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("aws_profile: example\naws_secret_access_key: nope\n")
    with pytest.raises(ConfigError, match="unknown setting"):
        load_settings(path)


def test_config_path_override(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DADSOPS_CONFIG", str(tmp_path / "alt.yaml"))
    assert config_path() == tmp_path / "alt.yaml"
    monkeypatch.delenv("DADSOPS_CONFIG")
    assert config_path() == Path(os.path.expanduser("~/.config/dadsops/config.yaml"))
