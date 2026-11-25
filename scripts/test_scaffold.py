#!/usr/bin/env python3
"""Tests of scaffold.py itself (run: python -m pytest scripts/test_scaffold.py, or python scripts/test_scaffold.py).

The full checks of a scaffolded project (fmt, clippy, tests, deny, validator) stay in README "Changing the forge".
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

SCAFFOLD = Path(__file__).resolve().parent / "scaffold.py"
REPO_URL = "https://github.com/example-org/rv-review-example"


def scaffold(profile: str, out: Path, *extra: str) -> Path:
    subprocess.run(
        [sys.executable, str(SCAFFOLD), "--profile", profile, "--name", "rv-review-example",
         "--title", "RV Review Example", "--description", "Scaffold test project.", "--out", str(out), *extra],
        check=True, capture_output=True, text=True)
    return out / "rv-review-example"


def check_licence_urls(profile: str) -> None:
    with tempfile.TemporaryDirectory(prefix="forge-scaffold-test-") as tmp:
        custom = scaffold(profile, Path(tmp) / "custom", "--repo-url", REPO_URL)
        default = scaffold(profile, Path(tmp) / "default")
        for f in ("LICENSE", "COMMERCIAL.md"):
            text = (custom / f).read_text(encoding="utf-8")
            assert REPO_URL in text, f"{profile} {f}: --repo-url not written"
            assert "github.com/Panacota96/" not in text, f"{profile} {f}: reference URL left"
            assert "rv-hashinv" not in text, f"{profile} {f}: reference name left"
            assert "https://github.com/Panacota96/rv-review-example" in (default / f).read_text(encoding="utf-8"), \
                f"{profile} {f}: default URL changed"
        notice = (custom / "LICENSE").read_text(encoding="utf-8").splitlines()[0]
        assert notice == f"Required Notice: Copyright 2026 Rakvor ({REPO_URL})", notice
        assert (custom / "COMMERCIAL.md").read_text(encoding="utf-8").count(f"<{REPO_URL}/issues>") == 3


def test_repo_url_reaches_licence_files_python() -> None:
    check_licence_urls("python")


def test_repo_url_reaches_licence_files_rust() -> None:
    check_licence_urls("rust")


if __name__ == "__main__":
    test_repo_url_reaches_licence_files_python()
    test_repo_url_reaches_licence_files_rust()
    print("ok")
