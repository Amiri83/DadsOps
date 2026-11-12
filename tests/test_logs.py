from pathlib import Path

import pytest

from dadsops.logs import log_file, tail


def test_log_file_is_the_tool_name_inside_the_log_dir(tmp_path: Path):
    assert log_file(tmp_path, "tool_a") == tmp_path.resolve() / "tool_a.log"


@pytest.mark.parametrize(
    "name", ["..", "../tool_a", "tool_a/../../x", "/etc/passwd", "a\\b", "", ".", "tool a"]
)
def test_traversal_shaped_names_are_rejected(tmp_path: Path, name: str):
    with pytest.raises(ValueError):
        log_file(tmp_path, name)


def test_tail_returns_the_last_lines(tmp_path: Path):
    path = tmp_path / "tool_a.log"
    path.write_text("".join(f"line {i}\n" for i in range(1, 1001)))

    lines = tail(path, block=64)

    assert len(lines) == 200
    assert lines[0] == "line 801"
    assert lines[-1] == "line 1000"


def test_tail_of_a_short_or_missing_file(tmp_path: Path):
    path = tmp_path / "tool_a.log"
    assert tail(path) == []
    path.write_text("only line")
    assert tail(path) == ["only line"]
