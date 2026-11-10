#!/usr/bin/env python3
"""Weighted language decision for a new Rakvor tool, written as an ADR with a Mermaid chart.

The scores (1-5) are the language council's baseline (references/language-council.md). Weights come from
a preset and can be adjusted per project; individual scores can be overridden with a reason when the
council's advocates argued for it (e.g. a Python-only parsing library raises Python's ecosystem score).

Usage:
  python decision_matrix.py --preset client --tool "RV JPEG Carve" --out docs/adr/0001-language.md
  python decision_matrix.py --preset internal --weight dfir_ecosystem=5 \
      --override "python.dfir_ecosystem=5:needs libyal pyevtx" --tool "RV EVTX Triage" --json
Presets: client (deliverables for clients/court), internal (examiner tooling), performance (huge images, 8 GB laptops).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

CRITERIA = {
    "speed": "Speed (throughput, startup)",
    "transferability": "Transferability (Win10/11 x64 + ARM64, Linux)",
    "storage": "Storage (executable / installer size)",
    "hardware": "Hardware / RAM (8 GB laptops, VMs without GPU)",
    "gui": "Windows GUI maturity",
    "cli_help": "CLI --help quality",
    "packaging": "Packaging, signing, antivirus false positives",
    "auditability": "Memory safety / court auditability",
    "velocity": "Team velocity",
    "dfir_ecosystem": "DFIR library ecosystem",
    "cicd": "CI/CD complexity (5 = simple)",
}
LANGS = {"rust": "Rust", "python": "Python", "c": "C (+asm)"}
PROFILE = {"rust": "A (Rust workspace: core + clap CLI + egui GUI)", "python": "B (Python: click/rich CLI + PySide6 GUI, Nuitka)",
           "c": "none: C is only used to wrap an existing C library from Profile A or B"}
SCORES = {  # council baseline, October 2026
    "rust": {"speed": 5, "transferability": 5, "storage": 4, "hardware": 5, "gui": 4, "cli_help": 5, "packaging": 4,
             "auditability": 5, "velocity": 3, "dfir_ecosystem": 4, "cicd": 5},
    "python": {"speed": 3, "transferability": 4, "storage": 2, "hardware": 3, "gui": 4, "cli_help": 5, "packaging": 2,
               "auditability": 3, "velocity": 5, "dfir_ecosystem": 5, "cicd": 3},
    "c": {"speed": 5, "transferability": 3, "storage": 5, "hardware": 5, "gui": 2, "cli_help": 2, "packaging": 4,
          "auditability": 2, "velocity": 3, "dfir_ecosystem": 4, "cicd": 3},
}
PRESETS = {
    "client": {"speed": 3, "transferability": 3, "storage": 2, "hardware": 2, "gui": 2, "cli_help": 2, "packaging": 2,
               "auditability": 3, "velocity": 2, "dfir_ecosystem": 0, "cicd": 1},
    "internal": {"speed": 1, "transferability": 1, "storage": 1, "hardware": 1, "gui": 2, "cli_help": 2, "packaging": 1,
                 "auditability": 1, "velocity": 4, "dfir_ecosystem": 4, "cicd": 0},
    "performance": {"speed": 4, "transferability": 2, "storage": 1, "hardware": 4, "gui": 1, "cli_help": 1, "packaging": 1,
                    "auditability": 3, "velocity": 1, "dfir_ecosystem": 1, "cicd": 1},
}
CLOSE_CALL = 3  # points; inside this margin the tie-breaker applies


def decide(preset: str, weights: dict, scores: dict) -> dict:
    totals = {lang: sum(weights[c] * scores[lang][c] for c in CRITERIA) for lang in LANGS}
    ranked = sorted(totals, key=lambda k: -totals[k])
    winner, runner = ranked[0], ranked[1]
    close = totals[winner] - totals[runner] <= CLOSE_CALL
    note = ""
    if winner == "c":  # C never ships a GUI+CLI product on its own in this forge
        winner, note = ranked[1], "C scored highest but has no supported GUI/CLI profile; next best chosen. Use C only behind FFI."
    elif close:
        tie = "rust" if preset in ("client", "performance") else "python"
        if winner != tie and runner == tie:
            note = f"Close call ({totals[winner]} vs {totals[runner]}): tie-breaker for {"an" if preset == "internal" else "a"} {preset} tool picks {LANGS[tie]}."
            winner = tie
        else:
            note = f"Close call ({totals[ranked[0]]} vs {totals[ranked[1]]}); tie-breaker for {"an" if preset == "internal" else "a"} {preset} tool agrees."
    return {"preset": preset, "weights": weights, "scores": scores, "totals": totals, "ranked": ranked, "winner": winner,
            "max": 5 * sum(weights.values()), "note": note}


def adr(tool: str, d: dict, overrides: list[str], context: str) -> str:
    w, s, t = d["weights"], d["scores"], d["totals"]
    rows = [f"| {label} | {w[c]} | " + " | ".join(str(s[lang][c]) for lang in LANGS) + " |" for c, label in CRITERIA.items() if w[c]]
    total_cells = " | ".join(f"**{t[lang]}**" if lang == d["winner"] else str(t[lang]) for lang in LANGS)
    pie = "\n".join(f'    "{LANGS[lang]}" : {t[lang]}' for lang in d["ranked"])
    over = "\n".join(f"- {o}" for o in overrides) or "- none"
    win = d["winner"]
    consequences = {
        "rust": "- Two static executables (CLI + GUI), no runtime to install; one core library powers both.\n"
                "- Contributors need Rust; Windows binaries are built on GitHub's `windows-latest` runners.\n"
                "- No hand-written assembly: hash and parsing crates already use SIMD/CPU intrinsics.",
        "python": "- Larger download (Python + Qt bundled by Nuitka) and slower start than Rust.\n"
                  "- Fast iteration and direct use of Python forensic libraries.\n"
                  "- PySide6 (LGPL) ships as separate DLLs; re-evaluate (Profile A) if the tool goes to clients or court.",
    }[win]
    return f"""# ADR 0001 — Implementation language: {LANGS[win]} (Profile {PROFILE[win].split(' ')[0]})

- Status: accepted
- Date: {dt.date.today().isoformat()}
- Deciders: language council (Rust, Python and C/assembly advocates + rebuttal round), approved by the project owner

## Context

{context or f"{tool} needs a CLI and a GUI on Windows 10/11, an auditable forensic trail and reports for clients."}

Weight preset: **{d['preset']}**. Score overrides argued by the council:

{over}

## Options scored (1–5, weighted)

| Criterion | Weight | {' | '.join(LANGS.values())} |
| --- | :-: | {' | '.join(':-:' for _ in LANGS)} |
{chr(10).join(rows)}
| **Weighted total** (max {d['max']}) | | {total_cells} |

```mermaid
pie showData
    title Weighted score ({d['preset']} preset)
{pie}
```

## Decision

Profile {PROFILE[win]}. {d['note']}

## Consequences

{consequences}
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preset", choices=sorted(PRESETS), required=True)
    ap.add_argument("--tool", default="The tool")
    ap.add_argument("--context", default="", help="one paragraph for the ADR's Context section")
    ap.add_argument("--weight", action="append", default=[], metavar="CRITERION=N")
    ap.add_argument("--override", action="append", default=[], metavar='LANG.CRITERION=N:"reason"')
    ap.add_argument("--out", type=Path, help="write the ADR markdown here")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    weights = dict(PRESETS[a.preset])
    for item in a.weight:
        k, v = item.split("=", 1)
        if k not in CRITERIA:
            sys.exit(f"unknown criterion {k}; choose from {', '.join(CRITERIA)}")
        weights[k] = int(v)
    scores = {lang: dict(v) for lang, v in SCORES.items()}
    notes = []
    for item in a.override:
        key, rest = item.split("=", 1)
        lang, crit = key.split(".", 1)
        value, _, reason = rest.partition(":")
        if lang not in scores or crit not in CRITERIA or not reason.strip():
            sys.exit(f"bad override {item!r}: format lang.criterion=score:reason (a reason is mandatory); "
                     f"lang in {', '.join(scores)}; criterion in {', '.join(CRITERIA)}")
        notes.append(f"{LANGS[lang]} · {CRITERIA[crit]}: {scores[lang][crit]} → {int(value)} ({reason.strip()})")
        scores[lang][crit] = max(1, min(5, int(value)))
    d = decide(a.preset, weights, scores)
    text = adr(a.tool, d, notes, a.context)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text, encoding="utf-8")
    if a.json:
        print(json.dumps({k: d[k] for k in ("preset", "totals", "ranked", "winner", "max", "note")} | {"overrides": notes}, indent=2))
    else:
        print(" > ".join(f"{LANGS[lang]} {d['totals'][lang]}" for lang in d["ranked"]) + f"   ⇒ {LANGS[d['winner']]} (Profile {PROFILE[d['winner']]})")
        if d["note"]:
            print(d["note"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
