#!/usr/bin/env python3
"""Checks that a generated Rakvor tool meets the forge contract.

Usage:
  python validate_project.py <project-dir>              static checks (seconds)
  python validate_project.py <project-dir> --run        + builds and runs the CLI on examples/sample (minutes)
  python validate_project.py <project-dir> --run --tests  + the project's own test suite
  add --json for machine-readable output (used by graders / CI)

Exit code 0 when every check passes, 1 otherwise. Standard library only (jsonschema/blake3 used if installed).
The project's .forge.json (written by scaffold.py) says which profile it is and how to call the CLI.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REQUIRED_FILES = [
    "README.md", "CHANGELOG.md", "CONTRIBUTING.md", "LICENSE", "NOTICE", "SECURITY.md", ".gitattributes", ".gitignore",
    ".forge.json", ".github/workflows/ci.yml", ".github/workflows/release.yml", "packaging/windows/installer.iss",
    "release-please-config.json", ".release-please-manifest.json",
    "schemas/statistics.schema.json", "schemas/item-technical.schema.json", "schemas/item-summary.schema.json",
    "schemas/item-custody.schema.json", "schemas/case.schema.json", "schemas/registry.schema.json", "tests/fake_tsa.py",
    "docs/architecture.md", "docs/forensic-audit.md", "docs/reports.md", "docs/usage-cli.md", "docs/usage-gui.md",
    "docs/development.md", "docs/adr/0001-language.md", "docs/case-folder.md",
    "docs/theory.md", "docs/admissibility.md", "docs/installation.md", "docs/usage.md",
    "examples/README.md", "examples/sample/expected_hashes.csv", "examples/sample/run.sh", "examples/sample/run.ps1",
    "tests/verify_chain.py", "tests/validate_reports.py",
]
REQUIRED_DIRS = ["src", "docs", "tests", "examples", "examples/sample/evidence"]
HELP_SECTIONS = ["DESCRIPTION:", "EXAMPLES:", "EXIT CODES:", "FILES WRITTEN:", "FORENSIC NOTES:"]
COMMON_FLAGS = ["--input", "--input-list", "--recursive", "--hash", "--out", "--lang", "--actor", "--case", "--progress",
                "--jurisdiction", "--tsa-url", "--no-timestamp", "--sign-cert", "--case-dir", "--evidence"]
CASE_FINDINGS = ["EVIDENCE_HASH_MISMATCH_WITH_CASE", "EVIDENCE_NOT_IN_CASE_REGISTRY"]
GENESIS = "0" * 64
SKIP_PARTS = {"target", ".venv", "dist", "node_modules", ".git"}


class Report:
    def __init__(self) -> None:
        self.checks: list[dict] = []

    def add(self, name: str, passed: bool, detail: str = "") -> bool:
        self.checks.append({"name": name, "passed": bool(passed), "detail": detail})
        return bool(passed)

    @property
    def failed(self) -> list[dict]:
        return [c for c in self.checks if not c["passed"]]


def read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


# ---------------------------------------------------------------- static checks

def static_checks(root: Path, r: Report) -> dict:
    forge = {}
    try:
        forge = json.loads(read(root / ".forge.json"))
    except json.JSONDecodeError:
        pass
    profile = forge.get("profile") or ("rust" if (root / "Cargo.toml").exists() else "python" if (root / "pyproject.toml").exists() else "")
    forge.setdefault("profile", profile)
    r.add("profile detected", profile in ("rust", "python"), profile or "no Cargo.toml / pyproject.toml / .forge.json")

    missing = [f for f in REQUIRED_FILES if not (root / f).is_file()]
    th, adm = read(root / "docs/theory.md"), read(root / "docs/admissibility.md")
    r.add("theory doc has a plain-language part and an expert part with diagrams",
          bool(re.search(r"^#+ .*Part A", th, re.M)) and bool(re.search(r"^#+ .*Part B", th, re.M)) and th.count("```mermaid") >= 3)
    r.add("admissibility doc covers Colombia, France/EU, ISO/IEC 27037 and US/UK, with gaps and an examiner checklist",
          all(k in adm for k in ("Ley 906", "Ley 527", "procédure pénale", "eIDAS", "27037", "27041", "Daubert", "702"))
          and re.search(r"^#+ .*[Gg]ap", adm, re.M) is not None and re.search(r"^#+ .*[Cc]hecklist", adm, re.M) is not None)
    lic = read(root / "LICENSE")
    r.add("licence is the Rakvor source-auditable licence (local use, audit rights, no redistribution)", "RAKVOR SOURCE-AUDITABLE LICENCE" in lic and "Audit right" in lic)
    r.add("required files present", not missing, ", ".join(missing))
    missing_dirs = [d for d in REQUIRED_DIRS if not (root / d).is_dir()]
    r.add("src/ docs/ tests/ examples/ present", not missing_dirs, ", ".join(missing_dirs))

    lic = read(root / "LICENSE")
    r.add("CHANGELOG follows Keep a Changelog", "## [Unreleased]" in read(root / "CHANGELOG.md") and "keepachangelog" in read(root / "CHANGELOG.md").lower())

    no_mermaid = []
    for md in [root / "README.md", root / "CONTRIBUTING.md", *sorted((root / "docs").rglob("*.md"))]:
        if md.exists() and "```mermaid" not in read(md):
            no_mermaid.append(str(md.relative_to(root)))
    r.add("every docs page has a Mermaid diagram", not no_mermaid, ", ".join(no_mermaid))

    broken = []
    for md in [root / "README.md", *sorted((root / "docs").rglob("*.md")), root / "examples" / "README.md"]:
        for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", read(md)):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (md.parent / target).exists():
                broken.append(f"{md.relative_to(root)} -> {target}")
    r.add("relative links in docs resolve", not broken, "; ".join(broken[:8]))

    ci, rel = read(root / ".github/workflows/ci.yml"), read(root / ".github/workflows/release.yml")
    r.add("CI tests on Linux and Windows", "ubuntu-latest" in ci and "windows-latest" in ci)
    r.add("CI has least-privilege permissions", re.search(r"^permissions:\s*\n\s+contents:\s*read", ci, re.M) is not None)
    r.add("release builds installer + portable zip", "ISCC.exe" in rel and "Compress-Archive" in rel)
    r.add("release publishes SHA256SUMS, SBOM and provenance",
          "SHA256SUMS" in rel and "sbom" in rel.lower() and "attest-build-provenance" in rel)
    rel_all = "".join(read(p) for p in (root / ".github" / "workflows").glob("*.yml"))
    rp = read(root / "release-please-config.json")
    if (root / "Cargo.toml").is_file():
        lib = "".join(read(p) for p in root.rglob("src/core/src/lib.rs"))
        r.add("release never changes the lockfile (product version from version.txt, crate version fixed)",
              'env!("RV_VERSION")' in lib and "Cargo.toml" not in rp)
    else:
        r.add("release never changes the lockfile (dynamic version, uv.lock does not pin the project)",
              'dynamic = ["version"]' in read(root / "pyproject.toml"))
    r.add("release has a guarded code-signing step", "signtool" in rel_all.lower() and "SIGN_" in rel_all)
    r.add("installer carries the Rakvor icon", "SetupIconFile=" in read(root / "packaging/windows/installer.iss"))
    tpls = [p for p in root.rglob("templates/*.html") if not SKIP_PARTS & set(p.parts)]
    joined = "".join(read(p) for p in tpls)
    r.add("HTML reports carry the brand band and a custody template",
          "brand" in joined and "logo_svg" in joined and any(p.name == "custody.html" for p in tpls))
    r.add("installer targets Windows 10+ per-user", "MinVersion=10.0" in read(root / "packaging/windows/installer.iss")
          and "PrivilegesRequired=lowest" in read(root / "packaging/windows/installer.iss"))

    # i18n: same keys in en/es/fr, every finding explains what it shows and does not prove
    cats = {}
    for lang in ("en", "es", "fr"):
        hits = list(root.glob(f"**/i18n/{lang}.json"))
        hits = [h for h in hits if "target" not in h.parts and ".venv" not in h.parts and "dist" not in h.parts]
        if hits:
            try:
                cats[lang] = json.loads(read(hits[0]))
            except json.JSONDecodeError:
                pass
    ok_langs = len(cats) == 3
    r.add("report wording in en, es, fr", ok_langs, ",".join(sorted(cats)))
    if ok_langs:
        def keys(v, p=""):
            return sorted(k for key, val in v.items() for k in (keys(val, f"{p}.{key}") if isinstance(val, dict) else [f"{p}.{key}"]))
        r.add("i18n catalogues have identical keys", keys(cats["en"]) == keys(cats["es"]) == keys(cats["fr"]))
        bad = [c for c, f in cats["en"].get("findings", {}).items() if not all(k in f for k in ("title", "shows", "does_not_prove"))]
        r.add("every finding has title / shows / does_not_prove", not bad and bool(cats["en"].get("findings")), ", ".join(bad))
        r.add("limitations are declared", bool(cats["en"].get("limitations")))
        missing_sections = [k for k in ("brand", "ui", "custody") if not cats["en"].get(k)]
        r.add("i18n has brand, GUI (ui) and custody wording", not missing_sections, ", ".join(missing_sections))
        r.add("Rakvor brand descriptor present in every language",
              all(bool(cats[lang].get("brand", {}).get("descriptor")) for lang in ("en", "es", "fr") if lang in cats))
        missing_case = [c for c in CASE_FINDINGS if c not in cats["en"].get("findings", {})]
        r.add("case-folder findings are worded (EVIDENCE_HASH_MISMATCH_WITH_CASE, EVIDENCE_NOT_IN_CASE_REGISTRY)",
              not missing_case, ", ".join(missing_case))

    # Case role: one constant in the code + "case_role" in .forge.json (the scaffold flips both)
    code_role = case_role_in_code(root, profile)
    r.add("CASE_ROLE constant is intake or analysis", code_role in ("intake", "analysis"),
          "Rust: pub const CASE_ROLE: CaseRole = CaseRole::Intake|Analysis in src/core/src/lib.rs; "
          "Python: CASE_ROLE = \"intake\"|\"analysis\" in core/__init__.py" if code_role is None else code_role)
    r.add(".forge.json case_role matches CASE_ROLE", forge.get("case_role") == code_role and code_role is not None,
          f".forge.json: {forge.get('case_role')!r}, code: {code_role!r}")
    forge["case_role_effective"] = code_role or forge.get("case_role") or "intake"

    # Architecture rules
    if profile == "rust":
        core = root / "src" / "core" / "src"
        prints = [str(p.relative_to(root)) for p in core.rglob("*.rs") if re.search(r"\b(e?println!|e?print!)\(", read(p).split("#[cfg(test)]")[0])]
        r.add("core never prints (events only)", core.is_dir() and not prints, ", ".join(prints))
        r.add("CLI and GUI crates exist", (root / "src/cli/src/main.rs").is_file() and (root / "src/gui/src/main.rs").is_file())
        # state.rs, or a state/ module folder (mod.rs plus submodules) when the GUI logic grows
        state_files = [root / "src/gui/src/state.rs"] if (root / "src/gui/src/state.rs").is_file() else sorted((root / "src/gui/src/state").glob("*.rs"))
        r.add("GUI logic in a testable state module", bool(state_files) and any("#[test]" in read(f) for f in state_files))
        r.add("static CRT for Windows builds", "crt-static" in read(root / ".cargo/config.toml"))
        r.add("Cargo.lock committed", (root / "Cargo.lock").is_file())
    elif profile == "python":
        pkg = next((p for p in (root / "src").iterdir() if p.is_dir() and (p / "core").is_dir()), None) if (root / "src").is_dir() else None
        prints = [str(p.relative_to(root)) for p in (pkg / "core").rglob("*.py") if re.search(r"(^|\s)print\(", read(p))] if pkg else ["no core package"]
        r.add("core never prints (events only)", pkg is not None and not prints, ", ".join(prints))
        r.add("CLI and GUI modules exist", bool(pkg) and (pkg / "cli.py").is_file() and (pkg / "gui" / "app.py").is_file())
        r.add("GUI logic in a testable state module", bool(pkg) and (pkg / "gui" / "state.py").is_file()
              and any("gui" in p.name for p in (root / "tests").glob("test_*.py")))
        r.add("uv.lock committed", (root / "uv.lock").is_file())
    return forge


def case_role_in_code(root: Path, profile: str) -> str | None:
    if profile == "rust":
        m = re.search(r"pub const CASE_ROLE:\s*CaseRole\s*=\s*CaseRole::(Intake|Analysis)\s*;", read(root / "src/core/src/lib.rs"))
        return m.group(1).lower() if m else None
    if profile == "python" and (root / "src").is_dir():
        for init in sorted((root / "src").glob("*/core/__init__.py")):
            m = re.search(r"^CASE_ROLE\s*=\s*[\"'](intake|analysis)[\"']", read(init), re.M)
            if m:
                return m.group(1)
    return None


# ---------------------------------------------------------------- dynamic checks

def cli_command(root: Path, forge: dict) -> list[str]:
    if forge.get("cli_command"):
        return list(forge["cli_command"])
    if forge["profile"] == "rust":
        # No manifest: pick the first binary target that is not the GUI.
        try:
            meta = json.loads(subprocess.run(["cargo", "metadata", "--no-deps", "--format-version", "1"], cwd=root,
                                             capture_output=True, text=True, timeout=300).stdout)
            bins = [(p["name"], t["name"]) for p in meta["packages"] for t in p["targets"] if "bin" in t["kind"]]
            pkg, binary = next((b for b in bins if "gui" not in b[1].lower()), bins[0])
            return ["cargo", "run", "-q", "-p", pkg, "--bin", binary, "--"]
        except (ValueError, KeyError, IndexError, StopIteration, subprocess.SubprocessError, OSError):
            return ["cargo", "run", "-q", "--"]
    scripts = re.search(r"\[project\.scripts\]\s*\n\s*\"?([\w.-]+)\"?\s*=", read(root / "pyproject.toml"))
    name = scripts.group(1) if scripts else forge.get("name", root.name)
    return ["uv", "run", "-q", name]


def run_cli(root: Path, base: list[str], *args, timeout=900, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    # RV_NO_TIMESTAMP=1: contract runs must not depend on a public TSA; trust_checks() uses a local one.
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1", "RV_NO_TIMESTAMP": "1"}
    for k, v in (env_extra or {}).items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    return subprocess.run([*base, *map(str, args)], cwd=root, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, env=env)


def file_digest(path: Path, algo: str) -> str:
    if algo == "blake3":
        import blake3  # type: ignore

        h = blake3.blake3()
    else:
        h = hashlib.new(algo.replace("-", "_"))
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def chain_ok(path: Path) -> bool:
    prev, seq = GENESIS, 1
    for line in read(path).splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        stored = e.pop("entry_hash", "")
        canon = json.dumps(e, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        if e.get("seq") != seq or e.get("prev_entry_hash") != prev or hashlib.sha256(canon).hexdigest() != stored:
            return False
        prev, seq = stored, seq + 1
    return seq > 1


def dynamic_checks(root: Path, forge: dict, r: Report, run_tests: bool) -> None:
    base = cli_command(root, forge)
    command = forge.get("run_command", "scan")
    build = run_cli(root, base, "--help")
    if not r.add("CLI builds and starts", build.returncode == 0, (build.stderr or "")[-600:]):
        return

    h = run_cli(root, base, command, "--help").stdout
    missing = [s for s in HELP_SECTIONS + ["Usage:"] if s not in h]
    r.add(f"`{command} --help` has every required section", not missing, ", ".join(missing))
    # input_mode "case" (.forge.json): the main verb works on a case folder (e.g. RV Hub's whole-case verify), not on
    # evidence files, so the evidence-input options are not expected and the sample is a copy of examples/sample/case.
    case_mode = forge.get("input_mode") == "case"
    flags = [f for f in COMMON_FLAGS if not (case_mode and f in ("--input", "--input-list", "--recursive", "--evidence"))]
    missing_flags = [f for f in flags if f not in h]
    r.add(f"`{command} --help` documents the common options", not missing_flags, ", ".join(missing_flags))
    r.add("every option shows a Default and an Example", h.count("Default:") >= len(flags) and h.count("Example:") >= len(flags),
          f"Default: x{h.count('Default:')}, Example: x{h.count('Example:')}")
    for sub in ("verify-log", "self-test", "timestamp", "verify-case"):
        r.add(f"`{sub}` subcommand exists", run_cli(root, base, sub, "--help").returncode == 0)
    r.add("self-test passes", run_cli(root, base, "self-test").returncode == 0)

    target = ["--case-dir", "."] if case_mode else ["-i", "."]
    r.add("three hash algorithms are refused (exit 2)", run_cli(root, base, command, *target, "--hash", "md5,sha1,sha256").returncode == 2)
    r.add("unknown language is refused (exit 2)", run_cli(root, base, command, *target, "--lang", "xx").returncode == 2)

    docs = run_cli(root, base, "docs-cli")
    r.add("docs/usage-cli.md matches --help (no drift)", docs.returncode == 0 and docs.stdout == read(root / "docs/usage-cli.md"),
          "regenerate with the hidden `docs-cli` command" if docs.returncode == 0 else docs.stderr[-300:])

    evidence = root / "examples" / "sample" / "evidence"
    before = {p: file_digest(p, "sha256") for p in evidence.rglob("*") if p.is_file()}
    with tempfile.TemporaryDirectory(prefix="forge-validate-") as tmp:
        out = Path(tmp) / "out"
        if case_mode:
            copy = sample_case_copy(root, forge, Path(tmp) / "case")
            res = run_cli(root, base, command, "--case-dir", copy, "--hash", "sha256,md5", "--lang", "all",
                          "--actor", "validator", "--progress", "jsonl", *forge.get("sample_args", []))
            out = latest_case_run(copy, forge) or out
        else:
            res = run_cli(root, base, command, "-i", evidence, "-r", "--hash", "sha256,md5", "--lang", "all", "--case", "VALIDATE-1",
                          "--actor", "validator", "--progress", "jsonl", "-o", out, *forge.get("sample_args", []))
        r.add("sample run completes (exit 0 or 3)", res.returncode in (0, 3), f"exit {res.returncode}: {res.stderr[-400:]}")
        events = []
        for line in res.stdout.splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        r.add("--progress jsonl emits run_started ... run_finished", bool(events) and events[0].get("event") == "run_started"
              and events[-1].get("event") == "run_finished")
        elapsed = [e.get("elapsed_ms", 0) for e in events if e.get("event") == "progress"]
        r.add("progress events carry monotonic elapsed_ms", bool(elapsed) and elapsed == sorted(elapsed))
        r.add("progress events carry done/total/bytes", all({"done", "total", "bytes_done", "bytes_total", "eta_ms"} <= e.keys()
                                                             for e in events if e.get("event") == "progress") and bool(elapsed))

        if case_mode:
            # <CASE>/runs/<tool>/<RUN-ID>/{logs,reports} hold the files directly
            logs = [out / "logs"] if (out / "logs" / "audit.jsonl").is_file() else []
            reports = [out / "reports"] if (out / "reports").is_dir() else []
        else:
            logs = sorted((out / "logs").glob("*")) if (out / "logs").exists() else []
            reports = sorted((out / "reports").glob("*")) if (out / "reports").exists() else []
        if not r.add("logs/<run-id>/ and reports/<run-id>/ created", bool(logs) and bool(reports)):
            return
        log, rep = logs[-1], reports[-1]
        need = ["audit.jsonl", "audit.log", "hashes.csv", "environment.json"]
        r.add("log folder complete", all((log / f).is_file() for f in need), ", ".join(f for f in need if not (log / f).is_file()))
        r.add("audit chain verifies (independent re-implementation)", chain_ok(log / "audit.jsonl"))
        r.add("`verify-log` accepts the intact log", run_cli(root, base, "verify-log", log).returncode == 0)
        tampered = Path(tmp) / "tampered.jsonl"
        tampered.write_text(read(log / "audit.jsonl").replace('"actor":"validator"', '"actor":"mallory"', 1), encoding="utf-8")
        r.add("`verify-log` detects an edited entry (exit 3)", run_cli(root, base, "verify-log", tampered).returncode == 3)
        actions = [json.loads(line).get("action") for line in read(log / "audit.jsonl").splitlines() if line.strip()]
        r.add("audit log records start, per-item actions and finish",
              actions[:1] == ["run_started"] and actions[-1:] == ["run_finished"] and sum(a.startswith("item_") for a in actions) >= 2)

        rows = list(csv.DictReader(read(log / "hashes.csv").splitlines()))
        mismatch = []
        for row in rows:
            p = Path(row.get("path", ""))
            if not p.is_absolute() and case_mode:
                p = (copy / p) if (copy / p).exists() else p
            for algo in ("sha256", "md5"):
                if p.is_file() and row.get(algo) and file_digest(p, algo) != row[algo]:
                    # An append-only log (the case log) read before the tool appended to it: the register's size says
                    # how many bytes were read, and that prefix must still hash to the recorded value.
                    size = int(row.get("size") or -1)
                    if p.name == "case-log.jsonl" and 0 <= size < p.stat().st_size and \
                            getattr(hashlib, algo.replace("-", "_"))(p.read_bytes()[:size]).hexdigest() == row[algo]:
                        continue
                    mismatch.append(f"{p.name}:{algo}")
        r.add("hash register matches an independent computation", (bool(rows) or case_mode) and not mismatch, ", ".join(mismatch))
        exp = list(csv.DictReader(read(root / "examples/sample/expected_hashes.csv").splitlines()))
        exp_bad = [e["file"] for e in exp if (evidence / e["file"]).is_file() and file_digest(evidence / e["file"], "sha256") != e.get("sha256")]
        r.add("examples/sample/expected_hashes.csv is correct", bool(exp) and not exp_bad, ", ".join(exp_bad))

        stats = ["statistics.json"] + [f"statistics.{lang}.{ext}" for lang in ("en", "es", "fr") for ext in ("html", "pdf")]
        r.add("statistics report in JSON + HTML + PDF (en/es/fr)", all((rep / f).is_file() for f in stats),
              ", ".join(f for f in stats if not (rep / f).is_file()))
        items = sorted((rep / "items").glob("*")) if (rep / "items").is_dir() else []
        per_item = ["technical.json", "summary.json", "custody.json"] + \
            [f"{k}.{lang}.{ext}" for k in ("technical", "summary") for lang in ("en", "es", "fr") for ext in ("html", "pdf")] + \
            [f"custody.{lang}.{ext}" for lang in ("es", "en") for ext in ("html", "pdf")]
        incomplete = [i.name for i in items if not all((i / f).is_file() for f in per_item)]
        r.add("one folder per item with technical + customer reports + custody form",
              bool(items) and (case_mode or len(items) == len(rows)) and not incomplete,
              f"{len(items)} folders for {len(rows)} items; incomplete: {', '.join(incomplete)}")
        pdfs = [p for p in rep.rglob("*.pdf") if p.is_file()]
        bad_pdf = [p.name for p in pdfs if not (p.read_bytes()[:5] == b"%PDF-" and b"%%EOF" in p.read_bytes()[-32:])]
        r.add("PDFs are well-formed", bool(pdfs) and not bad_pdf, ", ".join(bad_pdf[:5]))
        r.add("languages really differ (es/fr vs en)", read(rep / "statistics.es.html") != read(rep / "statistics.en.html")
              and read(rep / "statistics.fr.html") != read(rep / "statistics.en.html"))
        tech = next(iter(items), None)
        if tech:
            tj = json.loads(read(tech / "technical.json") or "{}")
            r.add("technical JSON has schema, hashes, findings, limitations",
                  tj.get("schema", "").startswith("rakvor.item-technical/") and (case_mode or len(tj.get("hashes", {})) == 2)
                  and "findings" in tj and bool(tj.get("limitations")))
        try:
            import jsonschema  # type: ignore

            schemas = {n: json.loads(read(root / "schemas" / f"{s}.schema.json")) for n, s in
                       (("statistics.json", "statistics"), ("technical.json", "item-technical"), ("summary.json", "item-summary"), ("custody.json", "item-custody"))
                       if (root / "schemas" / f"{s}.schema.json").is_file()}
            invalid = []
            for j in [p for p in rep.rglob("*.json") if p.is_file()][:300]:
                try:
                    if j.name not in schemas:
                        continue
                    jsonschema.validate(json.loads(read(j)), schemas[j.name])
                except jsonschema.ValidationError as e:
                    invalid.append(f"{j.name}: {e.message[:80]}")
            r.add("JSON reports validate against schemas/", not invalid, "; ".join(invalid[:3]))
        except ImportError:
            pass
        after = {p: file_digest(p, "sha256") for p in evidence.rglob("*") if p.is_file()}
        r.add("evidence unchanged after the run", before == after)

    trust_checks(root, base, command, evidence, r, forge)
    if case_mode:
        case_input_checks(root, base, command, forge, r)
    else:
        case_checks(root, base, command, evidence, forge, r)

    if run_tests:
        cmd = ["cargo", "test", "--workspace", "-q"] if forge["profile"] == "rust" else ["uv", "run", "-q", "pytest", "-q"]
        t = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=1800,
                           env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        r.add("project test suite passes", t.returncode == 0, (t.stdout + t.stderr)[-600:])


def sample_case_copy(root: Path, forge: dict, dest: Path) -> Path:
    """A fresh copy of the committed sample case (input_mode "case"), so validation never writes into the repo."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(root / forge.get("sample_case", "examples/sample/case"), dest)
    return dest


def latest_case_run(case: Path, forge: dict) -> Path | None:
    runs = sorted((case / "runs" / forge.get("name", "")).glob("*")) if (case / "runs" / forge.get("name", "")).is_dir() else []
    return runs[-1] if runs else None


def case_input_checks(root: Path, base: list[str], command: str, forge: dict, r: Report) -> None:
    """input_mode "case": the main verb on a copy of the sample case writes its run inside the case and links it;
    verify-case still accepts the case; an edited case log gives exit 3 and the case is not extended."""
    with tempfile.TemporaryDirectory(prefix="forge-case-") as tmp:
        case = sample_case_copy(root, forge, Path(tmp) / "CASE")
        before = {p.relative_to(case): file_digest(p, "sha256") for p in case.rglob("*")
                  if p.is_file() and p.name != "case-log.jsonl" and not p.is_relative_to(case / "runs" / forge.get("name", ""))}
        res = run_cli(root, base, command, "--case-dir", case, "--actor", "validator", "--progress", "jsonl", *forge.get("sample_args", []))
        r.add("case run (case input) completes (exit 0 or 3)", res.returncode in (0, 3), f"exit {res.returncode}: {res.stderr[-400:]}")
        run = latest_case_run(case, forge)
        if not r.add("run written to <CASE>/runs/<tool>/<RUN-ID>/logs and reports",
                     bool(run) and (run / "logs" / "audit.jsonl").is_file() and (run / "reports").is_dir()):
            return
        actions = [json.loads(line).get("action") for line in read(run / "logs" / "audit.jsonl").splitlines() if line.strip()]
        r.add("run audit starts with run_started, case_linked", actions[:2] == ["run_started", "case_linked"], ", ".join(map(str, actions[:3])))
        probs = case_problems(case)
        r.add("case log, registry digest and run links verify (independent re-implementation)", not probs, "; ".join(probs[:4]))
        after = {p.relative_to(case): file_digest(p, "sha256") for p in case.rglob("*")
                 if p.is_file() and p.name != "case-log.jsonl" and not p.is_relative_to(case / "runs" / forge.get("name", ""))}
        r.add("nothing outside the tool's own run folder and the case log changed", before == after,
              ", ".join(str(k) for k in set(before) ^ set(after) | {k for k in before if before.get(k) != after.get(k)})[:300])
        v = run_cli(root, base, "verify-case", case)
        r.add("`verify-case` accepts the case (exit 0)", v.returncode == 0, (v.stdout + v.stderr)[-300:])
        broken = sample_case_copy(root, forge, Path(tmp) / "BROKEN")
        log = broken / "case-log.jsonl"
        lines = read(log).splitlines(keepends=True)
        lines[0] = lines[0].replace('"case_id":"', '"case_id":"X', 1) if '"case_id":"' in lines[0] else lines[0].replace('"seq":0', '"seq":0 ', 1)
        log.write_text("".join(lines), encoding="utf-8")
        b = run_cli(root, base, "verify-case", broken)
        r.add("`verify-case` detects an edited case log (exit 3)", b.returncode == 3, (b.stdout + b.stderr)[-300:])
        n_runs = len(list(broken.glob("runs/*/*")))
        x = run_cli(root, base, command, "--case-dir", broken, "--progress", "none", "-o", Path(tmp) / "out-broken")
        r.add("a broken case is never extended (exit 3, no new run folder)", x.returncode == 3 and len(list(broken.glob("runs/*/*"))) == n_runs,
              f"exit {x.returncode}")


# ---------------------------------------------------------------- case folder (independent re-implementation)

def _canon(entry: dict) -> bytes:
    return json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _entries(path: Path) -> list[dict]:
    return [json.loads(line) for line in read(path).splitlines() if line.strip()]


def write_case_with_registry(case: Path, files: list[Path], algos: tuple[str, ...] = ("sha256", "md5")) -> int:
    """Pre-creates a case the way an intake tool would (for analysis-role projects), written independently of the
    tool: case.json, evidence/registry.json + .csv and a hash-chained case log (case_created, evidence_registered).
    Returns the number of registered items."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    (case / "evidence").mkdir(parents=True, exist_ok=True)
    pretty = lambda v: (json.dumps(v, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")  # noqa: E731
    who = {"tool": "forge-validator", "version": "1", "run_id": "VALIDATOR-INTAKE", "actor": "validator"}
    case_json = pretty({"schema": "rakvor.case/1", "case_id": "VALIDATE-CASE", "jurisdiction": "co", "created_utc": now, "created_by": who})
    (case / "case.json").write_bytes(case_json)
    items = []
    for n, f in enumerate(sorted(files, key=str), 1):
        items.append({"evidence_id": f"E{n:04d}", "path": os.path.normpath(os.path.abspath(f)), "size": f.stat().st_size,
                      "hashes": {a: file_digest(f, a) for a in algos},
                      "registered_by": {"tool": "forge-validator", "version": "1", "run_id": "VALIDATOR-INTAKE", "audit_seq": 0},
                      "registered_utc": now})
    reg = pretty({"schema": "rakvor.registry/1", "items": items})
    (case / "evidence" / "registry.json").write_bytes(reg)
    cols = [a for a in ("md5", "sha1", "sha256", "sha512", "sha3-256", "blake3") if a in algos]
    (case / "evidence" / "registry.csv").write_text(
        "evidence_id,path,size," + ",".join(cols) + ",run_id\n" +
        "".join(f"{i['evidence_id']},{i['path']},{i['size']}," + ",".join(i["hashes"][a] for a in cols) + ",VALIDATOR-INTAKE\n" for i in items),
        encoding="utf-8")
    prev, lines = GENESIS, []
    for seq, (action, details) in enumerate([
        ("case_created", {"case_id": "VALIDATE-CASE", "jurisdiction": "co", "schema": "rakvor.case/1",
                          "case_json_sha256": hashlib.sha256(case_json).hexdigest(), "registry_sha256": None,
                          "tool": "forge-validator", "version": "1"}),
        ("evidence_registered", {"tool": "forge-validator", "version": "1", "evidence_ids": [i["evidence_id"] for i in items],
                                 "added": [i["evidence_id"] for i in items], "registry_items": len(items),
                                 "registry_sha256": hashlib.sha256(reg).hexdigest()}),
    ], 1):
        e = {"seq": seq, "ts_utc": now, "run_id": "VALIDATOR-INTAKE", "actor": "validator", "action": action,
             "details": details, "prev_entry_hash": prev}
        e["entry_hash"] = hashlib.sha256(_canon(e)).hexdigest()
        prev = e["entry_hash"]
        lines.append(json.dumps(e, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    (case / "case-log.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(items)


def case_problems(case: Path) -> list[str]:
    """Independent verify-case: case-log chain, case.json and registry digests, every run's audit chain, the
    case_linked -> run_started link and the run_finished -> final audit entry link."""
    problems = []
    log = case / "case-log.jsonl"
    if not chain_ok(log):
        return ["case-log.jsonl chain broken or empty"]
    entries = _entries(log)
    if entries[0].get("action") != "case_created":
        problems.append("first case-log entry is not case_created")
    rec_case = entries[0].get("details", {}).get("case_json_sha256")
    if rec_case and hashlib.sha256((case / "case.json").read_bytes()).hexdigest() != rec_case:
        problems.append("case.json differs from its recorded digest")
    rec_reg = next((e["details"]["registry_sha256"] for e in reversed(entries) if isinstance(e.get("details", {}).get("registry_sha256"), str)), None)
    reg = case / "evidence" / "registry.json"
    if (rec_reg is None) != (not reg.is_file()) or (rec_reg and hashlib.sha256(reg.read_bytes()).hexdigest() != rec_reg):
        problems.append("registry.json does not match the digest recorded in the case log")
    finished = {e["run_id"]: e["details"] for e in entries if e.get("action") == "run_finished"}
    for e in (x for x in entries if x.get("action") == "run_started"):
        audit = case / e["details"]["run_dir"] / "logs" / "audit.jsonl"
        if not chain_ok(audit):
            problems.append(f"{e['run_id']}: audit chain broken or missing")
            continue
        run = _entries(audit)
        linked = next((x for x in run if x.get("action") == "case_linked"), {})
        if linked.get("details", {}).get("case_log_head_at_start") != e["entry_hash"]:
            problems.append(f"{e['run_id']}: case_linked does not point to its run_started entry")
        fin = finished.get(e["run_id"])
        if fin is None:
            problems.append(f"{e['run_id']}: no run_finished in the case log")
        elif fin.get("audit_entries") and next((x["entry_hash"] for x in run if x["seq"] == fin["audit_entries"]), None) != fin.get("audit_head"):
            problems.append(f"{e['run_id']}: run_finished audit head is not the run's final entry")
    return problems


def case_checks(root: Path, base: list[str], command: str, evidence: Path, forge: dict, r: Report) -> None:
    """Shared case folder end to end: intake-role tools register the sample, analysis-role tools verify a registry
    pre-created here; then verify-case (exit 0), tampering (exit 3), and for analysis a modified copy -> mismatch."""
    role = forge.get("case_role_effective", "intake")
    with tempfile.TemporaryDirectory(prefix="forge-case-") as tmp:
        case = Path(tmp) / "CASE"
        copies = Path(tmp) / "evidence"
        shutil.copytree(evidence, copies)
        files = [p for p in copies.rglob("*") if p.is_file()]
        if role == "analysis":
            n = write_case_with_registry(case, files)
            res = run_cli(root, base, command, "--case-dir", case, "--actor", "validator", "--progress", "jsonl", *forge.get("sample_args", []))
        else:
            n = len(files)
            res = run_cli(root, base, command, "-i", copies, "-r", "--case-dir", case, "--case", "VALIDATE-CASE", "--hash", "sha256,md5",
                          "--actor", "validator", "--progress", "jsonl", *forge.get("sample_args", []))
        r.add(f"case run ({role} role) completes (exit 0 or 3)", res.returncode in (0, 3), f"exit {res.returncode}: {res.stderr[-400:]}")
        try:
            cj = json.loads(read(case / "case.json") or "{}")
        except json.JSONDecodeError:
            cj = {}
        r.add("case.json written (schema rakvor.case/1)", cj.get("schema") == "rakvor.case/1" and cj.get("case_id") == "VALIDATE-CASE")
        probs = case_problems(case) if (case / "case-log.jsonl").is_file() else ["case-log.jsonl missing"]
        r.add("case log, registry digest and run links verify (independent re-implementation)", not probs, "; ".join(probs[:4]))
        audits = sorted(case.glob("runs/*/*/logs/audit.jsonl"))
        if not r.add("run written to <CASE>/runs/<tool>/<RUN-ID>/logs and reports", bool(audits) and (audits[-1].parent.parent / "reports").is_dir()):
            return
        actions = [json.loads(line).get("action") for line in read(audits[-1]).splitlines() if line.strip()]
        r.add("run audit starts with run_started, case_linked", actions[:2] == ["run_started", "case_linked"], ", ".join(map(str, actions[:3])))
        try:
            registry = json.loads(read(case / "evidence" / "registry.json") or "{}").get("items", [])
        except json.JSONDecodeError:
            registry = []
        if role == "analysis":
            ok = sum(1 for line in read(audits[-1]).splitlines() if line.strip() and json.loads(line).get("action") == "evidence_verified_against_case"
                     and json.loads(line)["details"].get("match") is True)
            r.add("every registered item verified against the case (evidence_verified_against_case)", ok == n, f"{ok} of {n}")
        else:
            r.add("every sample item registered (registry.json + evidence_registered in the audit)",
                  len(registry) == n and actions.count("evidence_registered") == n, f"registry {len(registry)}, audit {actions.count('evidence_registered')}, files {n}")
        techs = list((audits[-1].parent.parent / "reports").rglob("technical.json"))
        missing_ids = [t.parent.name for t in techs if not json.loads(read(t) or "{}").get("item", {}).get("evidence_id")]
        r.add("reports carry the evidence id of each item", bool(techs) and not missing_ids, ", ".join(missing_ids[:5]))
        v = run_cli(root, base, "verify-case", case)
        r.add("`verify-case` accepts the case (exit 0)", v.returncode == 0, (v.stdout + v.stderr)[-300:])
        if role == "analysis" and files:
            victim = sorted(files, key=str)[0]
            victim.write_bytes(victim.read_bytes() + b"tampered")
            t = run_cli(root, base, command, "--case-dir", case, "--progress", "none", *forge.get("sample_args", []))
            audits2 = sorted(case.glob("runs/*/*/logs/audit.jsonl"), key=lambda p: p.stat().st_mtime)
            text = read(audits2[-1]) if audits2 else ""
            r.add("analysis: a modified evidence copy gives EVIDENCE_HASH_MISMATCH_WITH_CASE (exit 3)",
                  t.returncode == 3 and "EVIDENCE_HASH_MISMATCH_WITH_CASE" in text, f"exit {t.returncode}")
        broken = Path(tmp) / "BROKEN"
        shutil.copytree(case, broken)
        log = broken / "case-log.jsonl"
        log.write_text(read(log).replace('"VALIDATE-CASE"', '"OTHER-CASE"', 1), encoding="utf-8")
        b = run_cli(root, base, "verify-case", broken)
        r.add("`verify-case` detects an edited case log (exit 3)", b.returncode == 3, (b.stdout + b.stderr)[-300:])
        before = len(list(broken.glob("runs/*/*")))
        x = run_cli(root, base, command, "--case-dir", broken, "--progress", "none", *forge.get("sample_args", []))
        r.add("a broken case is never extended (exit 3, no new run folder)", x.returncode == 3 and len(list(broken.glob("runs/*/*"))) == before,
              f"exit {x.returncode}")


def find_python_with_pyhanko(root: Path) -> str | None:
    cands = [root / ".venv" / "bin" / "python", root / ".venv" / "Scripts" / "python.exe", Path(sys.executable)]
    if os.environ.get("RV_TEST_PYTHON"):
        cands.insert(0, Path(os.environ["RV_TEST_PYTHON"]))
    for cand in cands:
        if cand.is_file() and subprocess.run([str(cand), "-c", "import pyhanko"], capture_output=True).returncode == 0:
            return str(cand)
    return None


def trust_checks(root: Path, base: list[str], command: str, evidence: Path, r: Report, forge: dict | None = None) -> None:
    """Timestamp + signed PDFs + jurisdiction custody form, end to end against tests/fake_tsa.py."""
    py = find_python_with_pyhanko(root)
    if not py or not (root / "tests" / "fake_tsa.py").is_file():
        r.add("trust features with the test PKI (skipped: no Python with pyhanko; set RV_TEST_PYTHON)", True)
        return
    with tempfile.TemporaryDirectory(prefix="forge-trust-") as tmp:
        pki, out = Path(tmp) / "pki", Path(tmp) / "out"
        srv = subprocess.Popen([py, str(root / "tests" / "fake_tsa.py"), "serve", str(pki)], stdout=subprocess.PIPE, text=True)
        try:
            url = (srv.stdout.readline() if srv.stdout else "").strip()
            env = {"RV_NO_TIMESTAMP": None, "RV_SIGN_PASSWORD": "test", "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
            case_mode = (forge or {}).get("input_mode") == "case"
            if case_mode:
                case = sample_case_copy(root, forge, Path(tmp) / "case")
                res = run_cli(root, base, command, "--case-dir", case, "--tsa-url", url, "--sign-cert", pki / "examiner.p12",
                              "--jurisdiction", "fr", "--progress", "none", *forge.get("sample_args", []), env_extra=env)
                out = latest_case_run(case, forge) or out
                logs = [out / "logs"] if (out / "logs").is_dir() else []
            else:
                res = run_cli(root, base, command, "-i", evidence, "-r", "--tsa-url", url, "--sign-cert", pki / "examiner.p12",
                              "--jurisdiction", "fr", "--progress", "none", "-o", out, *(forge or {}).get("sample_args", []), env_extra=env)
                logs = sorted((out / "logs").glob("*")) if (out / "logs").exists() else []
            log = logs[-1] if logs else None
            ok_ts = bool(log) and '"timestamp_obtained"' in read(log / "audit.jsonl") and any(log.glob("timestamp-*.tsr"))
            r.add("RFC 3161 timestamp obtained and stored with the log (local test TSA)", res.returncode in (0, 3) and ok_ts,
                  f"exit {res.returncode}: {res.stderr[-300:]}")
            # Only the reports are signed; derived evidence (extracted revisions, attachments) must stay byte-exact.
            pdfs = [p for p in out.rglob("*.pdf") if p.is_file() and p.name.split(".")[0] in ("statistics", "technical", "summary", "custody")]
            unsigned = [p.name for p in pdfs if b"/ByteRange" not in p.read_bytes()]
            r.add("every PDF report is signed with the examiner certificate", bool(pdfs) and not unsigned, ", ".join(unsigned[:5]))
            # A case's own jurisdiction overrides --jurisdiction (case-folder rules): expect that one in case mode.
            j = "fr"
            if case_mode:
                try:
                    j = json.loads(read(case / "case.json") or "{}").get("jurisdiction", "fr")
                except json.JSONDecodeError:
                    pass
            form = {"fr": "custody.fr.pdf", "co": "custody.es.pdf"}.get(j, "custody.fr.pdf")
            r.add(f"custody form follows the {'case' if case_mode else 'chosen'} jurisdiction ({j} + en)",
                  any(out.rglob(form)) and any(out.rglob("custody.en.pdf")))
            if log:
                v = run_cli(root, base, "verify-log", log, env_extra=env)
                r.add("`verify-log` validates the timestamp token", v.returncode == 0 and "timestamp" in (v.stdout + v.stderr).lower(),
                      (v.stdout + v.stderr)[-300:])
            off = Path(tmp) / "off"
            if case_mode:
                case2 = sample_case_copy(root, forge, Path(tmp) / "case-off")
                pending = run_cli(root, base, command, "--case-dir", case2, "--tsa-url", "http://127.0.0.1:9/", "--progress", "none",
                                  *forge.get("sample_args", []), env_extra={"RV_NO_TIMESTAMP": None})
                run2 = latest_case_run(case2, forge)
                off_logs = [run2 / "logs"] if run2 and (run2 / "logs").is_dir() else []
            else:
                pending = run_cli(root, base, command, "-i", evidence, "--tsa-url", "http://127.0.0.1:9/", "--progress", "none",
                                  "-o", off, *(forge or {}).get("sample_args", []), env_extra={"RV_NO_TIMESTAMP": None})
                off_logs = sorted((off / "logs").glob("*")) if (off / "logs").exists() else []
            r.add("unreachable TSA: run still finishes and logs timestamp_pending", pending.returncode in (0, 3) and bool(off_logs)
                  and '"timestamp_pending"' in read(off_logs[-1] / "audit.jsonl"), f"exit {pending.returncode}: {pending.stderr[-200:]}")
        finally:
            srv.terminate()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", type=Path)
    ap.add_argument("--run", action="store_true", help="build and run the CLI on examples/sample")
    ap.add_argument("--tests", action="store_true", help="also run the project's own test suite")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args()
    root = a.project.resolve()
    r = Report()
    forge = static_checks(root, r)
    if a.run and forge.get("profile"):
        tool = "cargo" if forge["profile"] == "rust" else "uv"
        if shutil.which(tool):
            dynamic_checks(root, forge, r, a.tests)
        else:
            r.add(f"{tool} available for --run", False, f"install {tool}")
    if a.json:
        print(json.dumps({"project": str(root), "profile": forge.get("profile"), "passed": len(r.checks) - len(r.failed),
                          "failed": len(r.failed), "checks": r.checks}, indent=2))
    else:
        for c in r.checks:
            print(f"[{'PASS' if c['passed'] else 'FAIL'}] {c['name']}" + (f"  — {c['detail']}" if c["detail"] and not c["passed"] else ""))
        print(f"\n{len(r.checks) - len(r.failed)}/{len(r.checks)} checks passed")
    return 0 if not r.failed else 1


if __name__ == "__main__":
    sys.exit(main())
