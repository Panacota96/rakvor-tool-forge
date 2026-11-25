"""The licence the tool ships with: PolyForm Noncommercial 1.0.0 under Rakvor's Required Notice, plus COMMERCIAL.md."""

import pathlib
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_licence_is_polyform_noncommercial_with_rakvor_notice():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.strip()]
    assert lines[0].startswith("Required Notice: Copyright ") and "Rakvor" in lines[0], lines[0]
    assert lines[1] == "# PolyForm Noncommercial License 1.0.0"
    for section in ("## Acceptance", "## Noncommercial Purposes", "## Definitions"):
        assert section in text, section
    assert (ROOT / "COMMERCIAL.md").is_file()


def test_package_metadata_and_installer_name_the_licence():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert meta["license"] == "PolyForm-Noncommercial-1.0.0"
    assert "COMMERCIAL.md" in meta["license-files"]
    iss = (ROOT / "packaging" / "windows" / "installer.iss").read_text(encoding="utf-8")
    assert "LicenseFile={#SourceDir}\\LICENSE" in iss.splitlines()
    assert '"COMMERCIAL.md"' in (ROOT / "packaging" / "build_windows.py").read_text(encoding="utf-8")
