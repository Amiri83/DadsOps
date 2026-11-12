"""The flat layout ships everything the app reads at runtime inside the package."""

import ast
import sys
import tomllib
from fnmatch import fnmatch
from pathlib import Path

from dadsops import app as app_module
from dadsops import paths

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE = REPO_ROOT / "dadsops"
PYPROJECT = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())


def test_flat_layout_package_is_configured():
    setuptools = PYPROJECT["tool"]["setuptools"]
    assert setuptools["packages"] == ["dadsops"]
    assert not (REPO_ROOT / "src").exists()
    assert paths.PACKAGE_DIR == PACKAGE
    assert app_module.PACKAGE_DIR == PACKAGE


def test_every_runtime_data_file_is_package_data():
    globs = PYPROJECT["tool"]["setuptools"]["package-data"]["dadsops"]
    data_files = [
        p.relative_to(PACKAGE).as_posix()
        for p in PACKAGE.rglob("*")
        if p.is_file() and p.suffix not in (".py", ".pyc")
    ]

    assert "default_tools.yaml" in data_files
    assert "templates/settings.html" in data_files
    assert "templates/logs.html" in data_files
    assert "static/style.css" in data_files
    assert "static/theme/dadsops-theme.css" in data_files
    assert "templates/error.html" in data_files
    missing = [f for f in data_files if not any(fnmatch(f, g) for g in globs)]
    assert missing == []


def test_packaged_paths_live_inside_the_package():
    assert paths.DEFAULT_REGISTRY.parent == PACKAGE
    assert paths.DEFAULT_REGISTRY.is_file()
    assert (paths.PACKAGE_DIR / "templates").is_dir()
    assert (paths.PACKAGE_DIR / "static").is_dir()


def test_bootstrap_modules_import_only_the_standard_library():
    """`python3 -m dadsops` must get as far as creating the venv with no deps installed."""
    allowed = set(sys.stdlib_module_names) | {"dadsops.cli", "dadsops.paths"}
    for name in ("__init__.py", "__main__.py", "cli.py", "paths.py"):
        tree = ast.parse((PACKAGE / name).read_text())
        for node in tree.body:
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module]
            else:
                continue
            for module in modules:
                assert module in allowed or module.split(".")[0] in sys.stdlib_module_names, (
                    f"{name} imports {module} at module level"
                )


def test_version_comes_from_the_package():
    assert PYPROJECT["project"]["dynamic"] == ["version"]
    assert PYPROJECT["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "dadsops.__version__"}
