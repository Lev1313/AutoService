import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from app.bootstrap import acquire_data_lock
from app.diagnostics import command
from app.version import VERSION


def test_only_one_normal_instance_can_use_data_directory(tmp_path):
    path = tmp_path / "data" / "autoservice.sqlite3"
    lock = acquire_data_lock(path)
    try:
        with pytest.raises(RuntimeError, match="уже открыт"):
            acquire_data_lock(path)
    finally:
        lock.unlock()
    reopened = acquire_data_lock(path)
    reopened.unlock()


def test_release_version_and_safe_diagnostics(tmp_path):
    project = Path(__file__).resolve().parents[1]
    assert (
        tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))["project"][
            "version"
        ]
        == VERSION
    )
    assert command(["--self-test"]) == 2
    existing = tmp_path / "existing"
    existing.mkdir()
    marker = existing / "do-not-change.txt"
    marker.write_text("unchanged")
    assert command(["--self-test", str(existing)]) == 2
    assert command(["--self-test-reopen", str(existing)]) == 2
    assert marker.read_text() == "unchanged"
    assert list(existing.iterdir()) == [marker]


def test_release_scenario_in_separate_processes(tmp_path):
    root = tmp_path / "release-check"
    for option in ("--self-test", "--self-test-reopen"):
        result = subprocess.run(
            [sys.executable, "main.py", option, str(root)],
            capture_output=True,
            timeout=60,
            check=False,
        )
        report = root / "release-check.json"
        assert result.returncode == 0, (result.stderr, report.read_text(encoding="utf-8"))
    report = json.loads((root / "release-check.json").read_text(encoding="utf-8"))
    assert report["stage"] == "passed"
    assert not report["frozen"]
    assert (root / "printed.pdf").stat().st_size > 1000
