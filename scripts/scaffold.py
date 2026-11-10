#!/usr/bin/env python3
"""Creates a new Rakvor tool project from a working template.

The templates in assets/template/<profile>/ are complete, tested projects (the reference tool
"rv-hashinv"). Scaffolding copies one and renames it consistently, so a new project starts green:
its tests pass, its CLI runs, its CI works. Then the domain logic (analyzer) is replaced.

Usage:
  python scaffold.py --profile rust   --name rv-jpegcarve --title "RV JPEG Carve" \
      --description "Carves JPEG files from raw disk images." --out ~/projects
  python scaffold.py --profile python --name rv-whatsapp-rsmf --title "RV WhatsApp to RSMF" --description "..." --out .

Options:
  --repo-url URL       GitHub URL written into README/Cargo/installer (default https://github.com/rakvor/<name>)
  --command NAME       planned main subcommand (default "scan"), stored as .forge.json `planned_command`. The
                       template's subcommand is always `scan` and `run_command` stays "scan" so the fresh scaffold
                       validates green; the CLI agent renames the verb in code, then sets run_command to it.
  --force              overwrite an existing destination folder
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
TEMPLATES = HERE / "assets" / "template"
# Packaged skills ship each template as one base64 text file (assets/template-<profile>.tar.gz.b64): skill uploads
# allow at most 200 files and no nested zips. scripts/pack_templates.py writes them; they are unpacked here on demand.
PACKED = HERE / "assets"


def profiles() -> list[str]:
    names = {p.name for p in TEMPLATES.iterdir() if p.is_dir()} if TEMPLATES.is_dir() else set()
    names |= {p.name[len("template-"):-len(".tar.gz.b64")] for p in PACKED.glob("template-*.tar.gz.b64")}
    return sorted(names)


def template_dir(profile: str) -> Path:
    if (TEMPLATES / profile).is_dir():
        return TEMPLATES / profile
    import base64
    import io
    import tarfile
    import tempfile

    packed = PACKED / f"template-{profile}.tar.gz.b64"
    dest = Path(tempfile.mkdtemp(prefix=f"forge-template-{profile}-"))
    data = base64.b64decode("".join(packed.read_text(encoding="ascii").split()))
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        tar.extractall(dest, filter="data")
    return dest
SKIP_DIRS = {"target", ".venv", "dist", "build", "__pycache__", ".git", "rakvor-out", "out", ".pytest_cache", ".ruff_cache"}
TEXT_SUFFIXES = {".rs", ".toml", ".py", ".md", ".json", ".yml", ".yaml", ".iss", ".ps1", ".sh", ".css", ".html", ".txt", ".lock",
                 ".gitignore", ".gitattributes", ""}
REF = {"name": "rv-hashinv", "module": "rv_hashinv", "title": "RV HashInv", "repo": "https://github.com/rakvor/rv-hashinv"}
GUID_RE = re.compile(r"\{\{[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\}")
DESC_RE = re.compile(r"(<!-- forge:description -->\n)(.*?)(\n<!-- /forge:description -->)", re.S)


def is_text(p: Path) -> bool:
    return p.suffix in TEXT_SUFFIXES or p.name in (".gitignore", ".gitattributes", "LICENSE", "NOTICE")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", required=True, choices=profiles())
    ap.add_argument("--name", required=True, help="kebab-case tool name, e.g. rv-jpegcarve")
    ap.add_argument("--title", required=True, help='display name, e.g. "RV JPEG Carve"')
    ap.add_argument("--description", required=True, help="one or two sentences for README and package metadata")
    ap.add_argument("--out", type=Path, default=Path("."), help="parent folder of the new project")
    ap.add_argument("--repo-url")
    ap.add_argument("--command", default="scan", help="main subcommand name")
    ap.add_argument("--case-role", choices=["analysis", "intake"], default="analysis",
                    help="analysis (verifies evidence against the case registry; default) or intake (registers evidence)")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    if not re.fullmatch(r"[a-z][a-z0-9]*(-[a-z0-9]+)+", a.name) or len(a.name) > 40:
        sys.exit("--name must be kebab-case with at least one dash, e.g. rv-jpegcarve (max 40 chars)")
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,20}", a.command):
        sys.exit("--command must be a short lowercase word, e.g. scan, convert, carve")
    module = a.name.replace("-", "_")
    repo = a.repo_url or f"https://github.com/rakvor/{a.name}"
    dest = (a.out / a.name).resolve()
    if dest.exists():
        if not a.force:
            sys.exit(f"{dest} exists (use --force to overwrite)")
        shutil.rmtree(dest)

    src = template_dir(a.profile)
    replacements = [(REF["repo"], repo), (REF["title"], a.title), (REF["name"], a.name), (REF["module"], module)]
    renamed, edited = 0, 0
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        if any(part in SKIP_DIRS for part in rel.parts[:-1]) or (path.is_dir() and path.name in SKIP_DIRS):
            continue
        new_rel = Path(*[part.replace(REF["module"], module).replace(REF["name"], a.name) for part in rel.parts])
        target = dest / new_rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        renamed += new_rel != rel
        if is_text(path) and "evidence" not in rel.parts:
            text = path.read_text(encoding="utf-8")
            new = text
            for old, value in replacements:
                new = new.replace(old, value)
            new = GUID_RE.sub(lambda _m: "{{" + str(uuid.uuid4()).upper() + "}", new)
            new = DESC_RE.sub(lambda m: m.group(1) + a.description + m.group(3), new)
            edited += new != text
            target.write_text(new, encoding="utf-8", newline="")
        else:
            shutil.copy2(path, target)

    # Installer identity: installer.iss got a fresh AppId above; the packaging test's table of Rakvor AppIds must
    # list it for the new tool and keep the reference tool's own entry (the rename above rewrote its name).
    iss, pkg = dest / "packaging/windows/installer.iss", dest / "src/cli/tests/packaging.rs"
    if iss.is_file() and pkg.is_file():
        m = re.search(r"^AppId=\{\{([0-9A-F-]{36})\}", iss.read_text(encoding="utf-8"), re.M)
        t = pkg.read_text(encoding="utf-8")
        ref_entry = re.search(rf'\("{re.escape(a.name)}", "([0-9A-F-]{{36}})"\),', t)
        size = re.search(r"const RAKVOR_APP_IDS: \[\(&str, &str\); (\d+)\]", t)
        if not (m and ref_entry and size):
            sys.exit(f"cannot register the new installer AppId in {pkg}")
        t = t.replace(ref_entry.group(0), f'("{REF["name"]}", "{ref_entry.group(1)}"),\n    ("{a.name}", "{m.group(1)}"),', 1)
        t = t.replace(size.group(0), size.group(0).replace(f"; {size.group(1)}]", f"; {int(size.group(1)) + 1}]"), 1)
        pkg.write_text(t, encoding="utf-8", newline="")

    today = dt.date.today().isoformat()
    changelog = dest / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\nAll notable changes to this project are documented here.\n"
        "The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses\n"
        "[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Entries are generated from\n"
        "[Conventional Commits](https://www.conventionalcommits.org/) by release-please and reviewed by a human.\n\n"
        f"## [Unreleased]\n\n### Added\n- Project scaffolded with rakvor-tool-forge ({a.profile} profile) on {today}.\n",
        encoding="utf-8")
    forge = {"forge_version": 1, "profile": a.profile, "name": a.name, "module": module, "title": a.title, "run_command": "scan", "planned_command": a.command, "case_role": a.case_role,
             "created": today,
             "cli_command": (["cargo", "run", "-q", "-p", f"{a.name}-cli", "--"] if a.profile == "rust" else ["uv", "run", "-q", a.name])}
    (dest / ".forge.json").write_text(json.dumps(forge, indent=2) + "\n", encoding="utf-8")
    # Case role constant (see references/case-folder.md): the reference tool is the intake tool.
    if a.case_role == "analysis":
        for f, old, new in ((dest / "src/core/src/lib.rs", "CaseRole::Intake;", "CaseRole::Analysis;"),
                            (dest / "src" / module / "core/__init__.py", 'CASE_ROLE = "intake"', 'CASE_ROLE = "analysis"'),
                            (dest / "src/cli/tests/describe.rs", 'const ROLE: &str = "intake";', 'const ROLE: &str = "analysis";')):
            if f.is_file():
                t = f.read_text(encoding="utf-8")
                if old not in t:
                    sys.exit(f"cannot set case role in {f}")
                f.write_text(t.replace(old, new, 1), encoding="utf-8")
    # Renaming changes identifier lengths, so lines can cross rustfmt's width: reformat, or CI's fmt check fails.
    if a.profile == "rust":
        if shutil.which("cargo") is None or subprocess.run(["cargo", "fmt", "--all"], cwd=dest).returncode != 0:
            print("warning: run `cargo fmt --all` in the new project before the first commit", file=sys.stderr)
    print(f"created {dest}  ({a.profile}; {edited} files adapted, {renamed} paths renamed)")
    print("next steps:")
    if a.profile == "rust":
        print(f"  cd {dest} && cargo test --workspace && cargo run -q -p {a.name}-cli -- docs-cli > docs/usage-cli.md")
    else:
        print(f"  cd {dest} && uv sync --extra gui && uv run pytest && uv run {a.name} docs-cli > docs/usage-cli.md")
    print(f"  python {HERE / 'scripts' / 'validate_project.py'} {dest} --run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
