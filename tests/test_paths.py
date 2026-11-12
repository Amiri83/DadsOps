from pathlib import Path

import pytest

from dadsops import paths
from dadsops.paths import DEFAULT_REGISTRY, checkout_root, ensure_user_registry, registry_path

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    """A fake $HOME so ~/.config/dadsops lands in tmp_path."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("DADSOPS_REGISTRY", raising=False)
    return home


@pytest.fixture
def installed(monkeypatch):
    """Behave like a pip install: no checkout around the package."""
    monkeypatch.setattr(paths, "checkout_root", lambda: None)


def test_this_repo_is_detected_as_a_checkout():
    assert checkout_root() == REPO_ROOT


def test_site_packages_is_not_a_checkout(tmp_path: Path):
    package_dir = tmp_path / "site-packages" / "dadsops"
    package_dir.mkdir(parents=True)
    assert checkout_root(package_dir) is None


def test_checkout_uses_the_in_repo_registry(home):
    assert registry_path() == REPO_ROOT / "tools.yaml"
    assert not (home / ".config").exists()


def test_installed_copies_the_packaged_default_once(home, installed):
    target = home / ".config" / "dadsops" / "tools.yaml"

    assert registry_path() == target
    assert target.read_bytes() == DEFAULT_REGISTRY.read_bytes()
    assert sorted(p.name for p in target.parent.iterdir()) == ["tools.yaml"], "no temp files"

    target.write_text("tools: []\n")
    assert registry_path() == target
    assert target.read_text() == "tools: []\n"


def test_existing_user_registry_is_never_overwritten(home, installed):
    target = home / ".config" / "dadsops" / "tools.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("# mine\ntools: []\n")

    assert registry_path() == target
    assert target.read_text() == "# mine\ntools: []\n"


def test_concurrent_first_run_keeps_the_file_that_won(tmp_path: Path, monkeypatch):
    target = tmp_path / "tools.yaml"
    real_link = paths.os.link

    def racing_link(src, dst):
        Path(dst).write_text("# written by the other process\n")
        real_link(src, dst)

    monkeypatch.setattr(paths.os, "link", racing_link)

    assert ensure_user_registry(target) == target
    assert target.read_text() == "# written by the other process\n"
    assert [p.name for p in tmp_path.iterdir()] == ["tools.yaml"]


@pytest.mark.parametrize("mode", ["checkout", "installed"])
def test_environment_override_wins(home, monkeypatch, tmp_path, mode):
    if mode == "installed":
        monkeypatch.setattr(paths, "checkout_root", lambda: None)
    monkeypatch.setenv("DADSOPS_REGISTRY", str(tmp_path / "custom.yaml"))

    assert registry_path() == tmp_path / "custom.yaml"
    assert registry_path(tmp_path / "flag.yaml") == tmp_path / "flag.yaml"
    assert not (home / ".config").exists()
