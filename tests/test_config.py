import os
from pathlib import Path

import pytest
import yaml

from dadsops import config as config_module
from dadsops.config import (
    AppSettings,
    ConfigError,
    Profile,
    Settings,
    load_settings,
    resolve,
    save_settings,
    settings_from_dict,
)
from dadsops.paths import config_path
from dadsops.registry import load_registry

PROFILES = (Profile("example", "us-east-1"), Profile("example-dev", "eu-west-1"))


def two_profiles(**kwargs) -> Settings:
    return Settings(profiles=PROFILES, default_profile="example", **kwargs)


def test_missing_file_gives_defaults(tmp_path: Path):
    settings = load_settings(tmp_path / "config.yaml")

    assert settings == Settings()
    assert settings.profiles == ()
    assert settings.log_dir == "~/.local/state/dadsops/logs"
    assert settings.app("tool_a") == AppSettings(port=None, profile=None, enabled=True)


@pytest.mark.parametrize("value", ["dark", "light", "neon"])
def test_legacy_theme_key_loads_and_is_ignored(tmp_path: Path, value: str):
    path = tmp_path / "config.yaml"
    path.write_text(f"theme: {value}\nlog_dir: /var/tmp/dadsops-logs\n")

    settings = load_settings(path)

    assert settings == Settings(log_dir="/var/tmp/dadsops-logs")
    assert not hasattr(settings, "theme")


def test_legacy_theme_key_is_dropped_on_the_next_save(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("theme: dark\n")

    save_settings(load_settings(path), path)

    assert "theme" not in yaml.safe_load(path.read_text())


def test_save_then_load_round_trip(tmp_path: Path):
    path = tmp_path / "sub" / "config.yaml"
    settings = two_profiles(
        log_dir=str(tmp_path / "logs"),
        apps={
            "tool_a": AppSettings(port=9101, profile="example-dev", extra_args=("--verbose",)),
            "tool_b": AppSettings(enabled=False),
        },
    )

    save_settings(settings, path)

    assert load_settings(path) == settings
    assert (path.stat().st_mode & 0o777) == 0o600
    assert [p.name for p in path.parent.iterdir()] == ["config.yaml"], "no temp files left"
    stored = yaml.safe_load(path.read_text())
    assert stored["profiles"] == [
        {"name": "example", "region": "us-east-1", "default": True},
        {"name": "example-dev", "region": "eu-west-1"},
    ]
    assert stored["apps"] == {
        "tool_a": {"port": 9101, "profile": "example-dev", "extra_args": ["--verbose"]},
        "tool_b": {"enabled": False},
    }


def test_apps_left_at_their_defaults_are_not_written(tmp_path: Path):
    path = tmp_path / "config.yaml"
    save_settings(Settings(apps={"tool_a": AppSettings()}), path)

    assert yaml.safe_load(path.read_text())["apps"] == {}


def test_save_is_atomic_when_replace_fails(tmp_path: Path, monkeypatch):
    path = tmp_path / "config.yaml"
    save_settings(two_profiles(), path)
    before = path.read_text()

    def broken_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(config_module.os, "replace", broken_replace)
    with pytest.raises(OSError, match="disk full"):
        save_settings(Settings(log_dir="/var/tmp/other"), path)

    assert path.read_text() == before
    assert [p.name for p in tmp_path.iterdir()] == ["config.yaml"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"log_dir": ""}, "log directory"),
        (
            {"profiles": (Profile("bad profile", "us-east-1"),), "default_profile": "bad profile"},
            "use letters",
        ),
        ({"profiles": (Profile("example", "Mars"),), "default_profile": "example"}, "region"),
        (
            {"profiles": (Profile("example", "us-east-1"),) * 2, "default_profile": "example"},
            "unique",
        ),
        ({"profiles": PROFILES}, "exactly one"),
        ({"profiles": PROFILES, "default_profile": "missing"}, "exactly one"),
        ({"default_profile": "example"}, "must be one of the listed"),
        ({"apps": {"tool_a": AppSettings(port=0)}}, "1 to 65535"),
        ({"apps": {"tool_a": AppSettings(port=65536)}}, "1 to 65535"),
        ({"apps": {"tool_a": AppSettings(port=True)}}, "whole number"),
        (
            {"apps": {"tool_a": AppSettings(port=9001), "tool_b": AppSettings(port=9001)}},
            "already used by tool_a",
        ),
        ({"apps": {"tool_a": AppSettings(profile="ghost")}}, "not in the profile list"),
        ({"apps": {"tool_a": AppSettings(extra_args=["--x"])}}, "list of strings"),
        ({"apps": {"tool_a": AppSettings(extra_args=("--x", 3))}}, "list of strings"),
        ({"apps": {"tool_a": AppSettings(extra_args=("",))}}, "non-empty"),
        ({"apps": {"tool_a": AppSettings(extra_args=("--secret-key=x",))}}, "looks like a secret"),
        ({"apps": {"tool_a": AppSettings(extra_args=("AKIAABCDEFGHIJKLMNOP",))}}, "secret"),
        ({"apps": {"../tool_a": AppSettings()}}, "app name"),
    ],
)
def test_invalid_settings_rejected(kwargs, message):
    with pytest.raises(ConfigError, match=message):
        Settings(**kwargs)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("log_dir: /var/tmp\naws_secret_access_key: nope\n", "unknown setting"),
        ("profiles:\n  - {name: example, region: us-east-1, session_token: x}\n", "unknown"),
        ("apps:\n  tool_a: {port: 9001, password: x}\n", "unknown"),
        ("apps:\n  tool_a: {extra_args: --verbose}\n", "list of strings"),
        ("profiles: example\n", "profiles must be a list"),
        (
            "profiles:\n"
            "  - {name: example, region: us-east-1, default: true}\n"
            "  - {name: example-dev, region: eu-west-1, default: true}\n",
            "exactly one",
        ),
        ("- just\n- a list\n", "must be a mapping"),
    ],
)
def test_invalid_files_rejected(tmp_path: Path, content: str, message: str):
    path = tmp_path / "config.yaml"
    path.write_text(content)
    with pytest.raises(ConfigError, match=message):
        load_settings(path)


def test_per_app_profile_falls_back_to_the_global_default():
    settings = two_profiles(apps={"tool_b": AppSettings(profile="example-dev")})

    assert settings.profile(settings.app("tool_a").profile) == Profile("example", "us-east-1")
    assert settings.profile(settings.app("tool_b").profile) == Profile("example-dev", "eu-west-1")


def test_per_app_settings_override_the_registry(write_registry, fake_tool_entry, tmp_path):
    tools = load_registry(
        write_registry([fake_tool_entry("tool_a", port=9001), fake_tool_entry("tool_b", port=9002)])
    )
    settings = two_profiles(
        log_dir=str(tmp_path / "logs"),
        apps={"tool_a": AppSettings(port=9101, enabled=False, extra_args=("--verbose",))},
    )

    launch_a, launch_b = resolve(tools, settings)

    assert (launch_a.tool.port, launch_a.enabled, launch_a.extra_args) == (
        9101,
        False,
        ("--verbose",),
    )
    assert launch_a.command()[-1] == "--verbose"
    assert (launch_b.tool.port, launch_b.enabled, launch_b.extra_args) == (9002, True, ())
    assert launch_a.log_dir == launch_b.log_dir == tmp_path / "logs"


def test_override_port_clashing_with_another_registry_port_is_rejected(
    write_registry, fake_tool_entry
):
    tools = load_registry(
        write_registry([fake_tool_entry("tool_a", port=9001), fake_tool_entry("tool_b", port=9002)])
    )

    with pytest.raises(ConfigError, match="tool_b: port 9002 is already used by"):
        resolve(tools, Settings(apps={"tool_a": AppSettings(port=9002)}))


def test_launch_env_forwards_profile_region_log_dir_and_port(write_registry, fake_tool_entry):
    tools = load_registry(write_registry([fake_tool_entry("tool_a", port=9001)]))
    (launch,) = resolve(tools, two_profiles(log_dir="/var/tmp/dadsops"))

    env = launch.env(base={"PATH": "/usr/bin"})

    assert env == {
        "PATH": "/usr/bin",
        "PORT": "9001",
        "AWS_PROFILE": "example",
        "AWS_REGION": "us-east-1",
        "DADSOPS_LOG_DIR": "/var/tmp/dadsops",
    }


def test_profile_placeholder_without_a_profile_is_a_clear_error(write_registry, fake_tool_entry):
    entry = fake_tool_entry("tool_a")
    entry["start_command"] += ["--profile", "{profile}"]
    (launch,) = resolve(load_registry(write_registry([entry])), Settings())

    assert "AWS_PROFILE" not in launch.env(base={})
    with pytest.raises(ConfigError, match=r"uses \{profile\} but no AWS profile"):
        launch.command()


def test_settings_from_dict_none_means_defaults():
    assert settings_from_dict(None) == Settings()


def test_config_path_override(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DADSOPS_CONFIG", str(tmp_path / "alt.yaml"))
    assert config_path() == tmp_path / "alt.yaml"
    monkeypatch.delenv("DADSOPS_CONFIG")
    assert config_path() == Path(os.path.expanduser("~/.config/dadsops/config.yaml"))
