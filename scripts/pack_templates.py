#!/usr/bin/env python3
"""Packs assets/template/<profile>/ into assets/template-<profile>.tar.gz.b64 (one text file per profile).

Run before packaging the skill: skill uploads accept at most 200 files and no nested zip files, while each template
is a complete project of ~90 files. scaffold.py unpacks the text file on demand when assets/template/<profile>/ is
absent (as in an installed skill).

Usage: python scripts/pack_templates.py [--stage DIR]
  --stage DIR  also copy the skill to DIR/<skill-name>/ without the unpacked templates, ready for package_skill.py
"""

from __future__ import annotations

import argparse
import base64
import io
import shutil
import tarfile
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent


def pack(profile_dir: Path) -> Path:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for f in sorted(profile_dir.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                info = tar.gettarinfo(str(f), arcname=f.relative_to(profile_dir).as_posix())
                info.mtime, info.uid, info.gid, info.uname, info.gname = 0, 0, 0, "", ""
                with open(f, "rb") as fh:
                    tar.addfile(info, fh)
    out = HERE / "assets" / f"template-{profile_dir.name}.tar.gz.b64"
    out.write_text("\n".join(textwrap.wrap(base64.b64encode(buf.getvalue()).decode(), 76)) + "\n", encoding="ascii")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", type=Path)
    a = ap.parse_args()
    for d in sorted((HERE / "assets" / "template").iterdir()):
        if d.is_dir():
            print(f"packed {pack(d).name}")
    if a.stage:
        dest = a.stage / HERE.name
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(HERE, dest, ignore=shutil.ignore_patterns("__pycache__", "template"))
        n = sum(1 for p in dest.rglob("*") if p.is_file())
        print(f"staged {dest} ({n} files)")


if __name__ == "__main__":
    main()
