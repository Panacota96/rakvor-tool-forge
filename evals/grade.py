#!/usr/bin/env python3
"""Grades one eval run: wraps validate_project.py --json --run and adds task-specific checks.

Usage: python grade.py <run-dir> <eval-name>
  <run-dir> contains outputs/<project>/ ; writes <run-dir>/grading.json (fields text/passed/evidence).
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
VALIDATOR = HERE.parent / "scripts" / "validate_project.py"
sys.path.insert(0, str(VALIDATOR.parent))
import validate_project as vp  # noqa: E402

SKIP = {"target", ".venv", "node_modules", "dist", "build", ".git", "__pycache__"}


def find_project(outputs: Path) -> Path | None:
    cands = [p.parent for p in outputs.rglob("Cargo.toml") if not SKIP & set(p.parts)] + \
            [p.parent for p in outputs.rglob("pyproject.toml") if not SKIP & set(p.parts)]
    return min(cands, key=lambda p: len(p.parts)) if cands else None


def files(root: Path, pattern: str):
    return [p for p in root.rglob(pattern) if p.is_file() and not SKIP & set(p.relative_to(root).parts)]


def text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def run_validator(project: Path) -> dict:
    r = subprocess.run([sys.executable, str(VALIDATOR), str(project), "--run", "--json"], capture_output=True, text=True, timeout=3600)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"passed": 0, "failed": 1, "checks": [{"name": "validator crashed", "passed": False, "detail": r.stderr[-500:]}]}


def check(validator: dict, name: str) -> tuple[bool, str]:
    for c in validator.get("checks", []):
        if c["name"] == name or c["name"].endswith(name) or c["name"].startswith(name):
            return c["passed"], c.get("detail") or ("passed" if c["passed"] else "failed")
    return False, f"validator did not run check '{name}' (earlier failure?)"


def adr_text(project: Path) -> str:
    hits = files(project, "*.md")
    adrs = [h for h in hits if "adr" in str(h).lower() or "decision" in h.name.lower()]
    return "\n".join(text(h) for h in adrs)


def catalogs(project: Path) -> dict:
    out = {}
    for lang in ("en", "es", "fr"):
        for p in files(project, f"{lang}.json"):
            try:
                out[lang] = json.loads(text(p))
                break
            except json.JSONDecodeError:
                pass
    return out


def try_run(project: Path, forge: dict, evidence: Path, out: Path) -> tuple[bool, str]:
    base = vp.cli_command(project, forge)
    tried = []
    for cmd in [forge.get("run_command"), "scan", "convert", "carve", "run", "process"]:
        if not cmd or cmd in tried:
            continue
        tried.append(cmd)
        r = vp.run_cli(project, base, cmd, "-i", evidence, "-o", out, "--progress", "none")
        if r.returncode in (0, 3):
            return True, f"`{cmd}` exit {r.returncode}"
        r = vp.run_cli(project, base, cmd, "--input", evidence, "--out", out)
        if r.returncode in (0, 3):
            return True, f"`{cmd}` (long options) exit {r.returncode}"
    return False, f"no run command worked (tried {tried})"


def logged_digests(out: Path) -> str:
    return "\n".join(text(p) for p in out.rglob("*") if p.is_file() and p.suffix in (".jsonl", ".log", ".json", ".csv"))


def main() -> int:
    run_dir, eval_name = Path(sys.argv[1]).resolve(), sys.argv[2]
    project = find_project(run_dir / "outputs")
    exps: list[dict] = []

    def add(t: str, ok: bool, ev: str) -> None:
        exps.append({"text": t, "passed": bool(ok), "evidence": ev[:600]})

    if project is None:
        add("A project (Cargo.toml or pyproject.toml) exists in outputs/", False, "none found")
        (run_dir / "grading.json").write_text(json.dumps({"expectations": exps, "summary": {"passed": 0, "failed": 1, "total": 1, "pass_rate": 0.0}}, indent=2))
        return 0
    v = run_validator(project)
    forge = {}
    try:
        forge = json.loads(text(project / ".forge.json"))
    except json.JSONDecodeError:
        pass
    forge.setdefault("profile", "rust" if (project / "Cargo.toml").exists() else "python")
    failed = [c["name"] for c in v.get("checks", []) if not c["passed"]]
    add("The project passes validate_project.py --run (all contract checks)", not failed and v.get("passed", 0) > 0,
        f"{v.get('passed')}/{v.get('passed', 0) + v.get('failed', 0)} passed; failing: {', '.join(failed[:12])}")
    adr = adr_text(project)
    rs_files = files(project, "*.rs")

    if eval_name.startswith("es-evidence"):
        add("The project uses Rust (Profile A) and docs/adr/0001-language.md explains the choice",
            (project / "Cargo.toml").exists() and "rust" in adr.lower(), f"Cargo.toml={(project / 'Cargo.toml').exists()}, ADR chars={len(adr)}")
        cats = catalogs(project)
        def has(code_re):
            return all(any(re.search(code_re, c, re.I) and isinstance(f, dict) and {"shows", "does_not_prove"} <= f.keys()
                           for c, f in cats.get(lang, {}).get("findings", {}).items()) for lang in ("en", "es", "fr"))
        add("Findings for duplicates and extension/content mismatch exist with shows / does_not_prove in en, es and fr",
            has("DUPLIC") and has("MISMATCH|EXTENSION"), f"catalogues: {sorted(cats)}")
        ok, ev = check(v, "three hash algorithms are refused (exit 2)")
        add("The CLI refuses three hash algorithms with exit code 2", ok, ev)
        ok, ev = check(v, "one folder per item with technical + customer reports")
        add("Each item gets a separate technical and customer report in HTML, PDF and JSON", ok, ev)

    elif eval_name.startswith("en-whatsapp"):
        add("The project uses Python (Profile B) and the ADR explains why (internal tool, team velocity, libraries)",
            (project / "pyproject.toml").exists() and "python" in adr.lower() and re.search(r"velocity|internal", adr, re.I) is not None,
            f"pyproject={(project / 'pyproject.toml').exists()}, ADR chars={len(adr)}")
        ev_dir = project / "examples" / "sample" / "evidence"
        if not ev_dir.is_dir():
            ev_dir = next((p for p in (project / "examples").rglob("*") if p.is_dir()), project / "examples")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            ok, ev = try_run(project, forge, ev_dir, out)
            outputs = [p for p in out.rglob("*") if p.is_file() and (p.suffix.lower() == ".rsmf" or
                       (p.suffix.lower() == ".eml" and b"X-RSMF" in p.read_bytes()[:4096]))] if out.exists() else []
            add("A sample run converts the synthetic WhatsApp export(s) in examples/ into RSMF output files",
                ok and bool(outputs), f"{ev}; RSMF files: {[p.name for p in outputs][:5]}")
            logs = logged_digests(out) if out.exists() else ""
            missing = [p.name for p in outputs if hashlib.sha256(p.read_bytes()).hexdigest() not in logs]
            add("Every RSMF output file's digest is recorded in the audit log", bool(outputs) and not missing,
                f"{len(outputs)} outputs, digests missing for: {missing[:5]}")
        phones = []
        for p in files(project / "examples", "*"):
            if p.suffix.lower() in (".txt", ".csv", ".json", ".md", ".vcf"):
                phones += re.findall(r"\+\d[\d \-]{9,16}\d", text(p))
        real = [n for n in phones if not re.search(r"555|0000|\+0|\+99|1234", n)]
        add("examples/ contains only synthetic chat data (no real phone numbers or names of real people)", not real,
            f"{len(phones)} phone-like strings; suspicious: {real[:5]}")

    elif eval_name.startswith("fr-jpeg"):
        add("The project uses Rust and the ADR uses performance/hardware criteria",
            (project / "Cargo.toml").exists() and "rust" in adr.lower() and re.search(r"RAM|hardware|mat[ée]riel|performance", adr, re.I) is not None,
            f"Cargo.toml={(project / 'Cargo.toml').exists()}, ADR chars={len(adr)}")
        whole = []
        for p in rs_files:
            src = text(p).split("#[cfg(test)]")[0]
            if "/tests/" in str(p) or "build.rs" in p.name:
                continue
            for m in re.finditer(r"fs::read\(|read_to_end\(", src):
                whole.append(f"{p.relative_to(project)}:{src[:m.start()].count(chr(10)) + 1}")
        py_whole = [str(p.relative_to(project)) for p in files(project, "*.py") if "test" not in p.name and re.search(r"\.read\(\)", text(p))]
        add("The carving reads the image in a streaming way (no whole-file reads of evidence in the core)", not whole and not py_whole,
            f"whole-file reads: {(whole + py_whole)[:6]}")
        ev_dir = project / "examples" / "sample" / "evidence"
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            ok, ev = try_run(project, forge, ev_dir, out)
            jpgs = [p for p in out.rglob("*") if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg")] if out.exists() else []
            logs = logged_digests(out) if out.exists() else ""
            missing = [p.name for p in jpgs if hashlib.sha256(p.read_bytes()).hexdigest() not in logs]
            add("Carved JPEG files are written under the run's output folder and each one's digest is logged",
                ok and bool(jpgs) and not missing, f"{ev}; {len(jpgs)} carved; digest missing: {missing[:5]}")
        asm = [str(p.relative_to(project)) for ext in ("*.asm", "*.s", "*.S", "*.nasm") for p in files(project, ext)]
        asm += [str(p.relative_to(project)) for p in rs_files if re.search(r"\b(global_)?asm!\(", text(p))]
        asm += [str(p.relative_to(project)) for p in files(project, "build.rs") if "nasm" in text(p).lower()]
        add("No hand-written assembly anywhere in the project", not asm, f"assembly: {asm[:5]}")

    passed = sum(e["passed"] for e in exps)
    summary = {"passed": passed, "failed": len(exps) - passed, "total": len(exps), "pass_rate": round(passed / len(exps), 3)}
    (run_dir / "grading.json").write_text(json.dumps({"expectations": exps, "summary": summary,
                                                      "validator": {"passed": v.get("passed"), "failed": v.get("failed")}}, indent=2))
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
