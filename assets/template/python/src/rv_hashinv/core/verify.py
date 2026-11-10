"""verify-log: the audit chain, its RFC 3161 timestamps and, because a hash chain alone cannot show that
entries were removed from its end, a truncation check against the records kept outside the log:

* the timestamp: timestamp-<seq>.json names the entry it covers (covers_seq) and the token signs that entry's
  hash, so the log must still contain that entry with that hash;
* the case log: for a run inside a case folder (<CASE>/runs/<tool>/<RUN-ID>/logs), run_finished in
  case-log.jsonl records the run's final audit head and entry count (audit_head, audit_entries).

Entries after the last anchored one cannot be proven complete; the report says how many there are. Without a
timestamp or a case record, truncation is not detectable and the report says so.
Same rules and wording as the Rust profile (src/core/src/verify.rs).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .audit import verify as verify_chain
from .inputs import normalize_path
from .trust import verify_timestamps_with

NO_ANCHOR = "truncation not detectable without a timestamp or case record"
CASE_FILE, CASE_LOG = "case.json", "case-log.jsonl"


@dataclass
class LogVerification:
    path: Path
    entries: int = 0
    problems: list[str] = field(default_factory=list)  # anything here means exit 3
    timestamps: list[str] = field(default_factory=list)
    truncation: str = ""
    warnings: list[str] = field(default_factory=list)


def _entries(path: Path) -> list[dict]:
    out = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def case_of_log_dir(log_dir: Path) -> Path | None:
    """<CASE> when log_dir is <CASE>/runs/<tool>/<RUN-ID>/logs."""
    p = Path(normalize_path(log_dir))
    if p.name != "logs" or len(p.parents) < 4 or p.parents[2].name != "runs":
        return None
    root = p.parents[3]
    return root if (root / CASE_FILE).is_file() else None


def _entries_count(n: int) -> str:
    return "1 entry" if n == 1 else f"{n} entries"


def verify_log(path: Path, tsa_anchors: list | None = None) -> LogVerification:
    """Verifies path (an audit.jsonl). tsa_anchors: CA bundle (trust.load_ca_bundle) to also validate each
    timestamp's TSA certificate chain at genTime. Raises OSError only when the log cannot be opened."""
    path = Path(path)
    directory = path.parent
    v = LogVerification(path)
    n, problems = verify_chain(path)
    if problems:
        v.problems = problems
        return v
    v.entries = n
    entries = _entries(path)
    # Tokens are driven by the log's timestamp_obtained entries (a recorded token whose .tsr or metadata is
    # missing is a problem, never a skipped check); every token's TSA signature is verified.
    tokens = verify_timestamps_with(path, tsa_anchors)
    v.problems += tokens.problems
    # A covered entry beyond the log's end is reported by verify_timestamps_with ("truncated: ... not in the log").
    anchors: list[tuple[int, str]] = [(seq, f"the RFC 3161 timestamp ({file.replace('.tsr', '.json')})") for seq, file in tokens.covers if seq <= n]
    # One line per token: what its TSA signature and chain check established, when every token verified.
    v.timestamps = tokens.tokens if len(tokens.tokens) == len(tokens.listed) else tokens.listed

    case = case_of_log_dir(directory)
    if case is not None:
        _case_anchor(case, entries, n, v, anchors)

    if not anchors:
        v.truncation = NO_ANCHOR
    else:
        s, by = max(anchors, key=lambda a: a[0])
        if s == n:
            v.truncation = f"truncation: the final entry #{n} is anchored by {by}"
        else:
            later = [str(e.get("action", "?")) for e in entries if (e.get("seq") or 0) > s]
            v.truncation = (
                f"truncation: entries up to #{s} are anchored by {by}; {_entries_count(n - s)} after it ({', '.join(later)}) "
                "cannot be proven complete"
            )
    return v


def _case_anchor(case: Path, entries: list[dict], n: int, v: LogVerification, anchors: list[tuple[int, str]]) -> None:
    """Compares the end of the run's log with run_finished in the case log."""
    case_log = case / CASE_LOG
    try:
        _, broken = verify_chain(case_log)
    except OSError as e:
        broken = [str(e)]
    if broken:
        v.problems.append(f"{case_log} is broken (run verify-case): it cannot vouch for this log's length")
        return
    run_id = (entries[0] if entries else {}).get("run_id", "")
    case_entries = _entries(case_log)

    def mine(action: str) -> dict | None:
        return next((e for e in case_entries if e.get("action") == action and e.get("run_id") == run_id), None)

    fin = mine("run_finished")
    if fin is None:
        if mine("run_started") is not None:
            v.warnings.append(f"the case log has no run_finished for run {run_id} (still running, or interrupted)")
        else:
            v.warnings.append(f"run {run_id} is not announced in the case log")
        return
    details = fin.get("details") or {}
    recorded = details.get("audit_entries") or 0
    if not recorded:
        return
    if recorded > n:
        v.problems.append(f"truncated: the case log records {recorded} entries at run_finished, but the log ends at entry #{n}")
        return
    at = next((e for e in entries if e.get("seq") == recorded), None)
    if (at or {}).get("entry_hash") != details.get("audit_head"):
        v.problems.append(f"entry #{recorded} is not the audit head recorded in the case log at run_finished")
        return
    for later in (e for e in entries if (e.get("seq") or 0) > recorded):
        if later.get("action") != "timestamp_obtained":
            v.problems.append(f"entry #{later.get('seq')} ({later.get('action')}) was appended after run_finished")
    anchors.append((recorded, "the case log (run_finished)"))
