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
import gzip
import io
import shutil
import subprocess
import tarfile
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent


def tracked_files(profile_dir: Path) -> dict[str, int]:
    """Git-tracked files under profile_dir -> tar mode (0o775 if git records it executable, else 0o664).

    Packing only tracked files keeps caches (.ruff_cache, target/, .venv) out of the bundle, and taking the mode from
    the git index instead of the file system makes the bundle identical whether it is packed on Windows or Linux."""
    out = subprocess.run(["git", "ls-files", "-s", "-z", "--", "."], cwd=profile_dir, capture_output=True, check=True).stdout
    files = {}
    for entry in filter(None, out.decode("utf-8").split("\0")):
        meta, path = entry.split("\t", 1)
        files[path] = 0o775 if meta.split()[0] == "100755" else 0o664
    return files


def checkout_eol(profile_dir: Path) -> dict[str, bytes]:
    """Line ending each tracked text file gets in a plain (Linux, autocrlf off) checkout.

    That is what the bundle stores: CRLF where .gitattributes says eol=crlf (installer, PowerShell), LF otherwise.
    A Windows checkout with core.autocrlf=true holds CRLF copies of everything, so the working tree alone would give
    a different bundle on each OS. Files git stores with CRLF or as binary are packed as they are."""
    out = subprocess.run(["git", "ls-files", "--eol", "-z", "--", "."], cwd=profile_dir, capture_output=True, check=True).stdout
    eol = {}
    for entry in filter(None, out.decode("utf-8").split("\0")):
        meta, path = entry.split("\t", 1)
        if meta.split()[0] == "i/lf":
            eol[path] = b"\r\n" if "eol=crlf" in meta else b"\n"
    return eol


def pack(profile_dir: Path) -> Path:
    buf = io.BytesIO()
    # mtime=0 in the gzip header too, so packing twice gives the same bytes.
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz, tarfile.open(fileobj=gz, mode="w") as tar:
        eol = checkout_eol(profile_dir)
        for rel, mode in sorted(tracked_files(profile_dir).items()):
            data = (profile_dir / rel).read_bytes()
            if rel in eol:
                data = data.replace(b"\r\n", b"\n").replace(b"\n", eol[rel])
            info = tarfile.TarInfo(rel)
            info.size, info.mode = len(data), mode
            info.mtime, info.uid, info.gid, info.uname, info.gname = 0, 0, 0, "", ""
            tar.addfile(info, io.BytesIO(data))
    out = HERE / "assets" / f"template-{profile_dir.name}.tar.gz.b64"
    out.write_text("\n".join(textwrap.wrap(base64.b64encode(buf.getvalue()).decode(), 76)) + "\n",
                   encoding="ascii", newline="\n")
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
