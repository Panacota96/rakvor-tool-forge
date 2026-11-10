# Reports contract

Every run: **one statistics report**. Every item: **two separate reviews** — a hyper-detailed technical review
and a concise customer summary — always in their own folder, even for a single input. Each report exists as
JSON (machine-readable, language-neutral, schema-versioned) plus HTML and PDF per selected language (EN/ES/FR).

```text
<OUT>/reports/<RUN-ID>/
  statistics.json  statistics.<lang>.html  statistics.<lang>.pdf
  items/<NNNNN>-<sanitised name ≤60>/
     technical.json  technical.<lang>.html  technical.<lang>.pdf
     summary.json    summary.<lang>.html    summary.<lang>.pdf
     custody.json    custody.<es|fr>.html/pdf (per --jurisdiction)   custody.en.html/pdf
```

Scale rule: an item report must not grow with the size of the run. Lists of *other* items (duplicate partners,
siblings) are capped — `capped_list` 10 names, `capped_paths` 20 paths, then "… (+N)"; the full list lives once,
in the statistics report / hashes.csv. Learned the hard way: uncapped, 50k files gave 3.1 GB and a 1,222-page PDF.
For very large runs an `--item-reports flagged` option (only items with findings) is a reasonable domain addition.

## Brand (Rakvor)

| Token | Value | Use |
| --- | --- | --- |
| ink / panel | `#0D1117` / `#1A1F26` | header band, GUI background |
| blue → violet | `#2A5CFF` `#4D23FF` `#6B00FF` | accent rules, headings, buttons |
| cyan | `#00E5FF` | highlights, progress |
| light | `#E6E6E6` | text on dark |

Header band: placeholder RV mark (`templates/rv-mark.svg`, inlined as `logo_svg`), title, descriptor
("FORENSICS | INCIDENT RESPONSE | SECURITY") and the localized tagline from i18n `brand`. Body stays light so pages
print well. The PDF draws the same band and footer. Replace the placeholder mark by swapping the SVG/PNG/ICO files
only — nothing else references the shape.

## Content

| Section | Statistics | Technical | Customer summary |
| --- | :-: | :-: | :-: |
| Run id, case, examiner, start/end UTC, duration, interface | ✔ | ✔ | case + date |
| Algorithms, totals, throughput, errors | ✔ | | |
| Findings by severity / by code, items by detected type, duplicate groups | ✔ | | |
| Full path, size, modified, extension, detected type, status | | ✔ | name + status |
| All digests | | ✔ | primary only ("digital fingerprint", explained) |
| Findings: severity, title, code, **what it shows**, **what it does not prove** | | ✔ | title + shows, plain words |
| Method | | ✔ | "What we did" |
| Audit entries covering the item (`#first – #last`), log folder | | ✔ | |
| Limitations | ✔ | ✔ | |
| Next step (ok / attention / problem) | | ✔ | ✔ |
| Audit chain head + entry count; trusted timestamp (gen time, TSA, covered entry) or "pending" warning | ✔ | | |
| Signature line (signer subject + fingerprint) when PDFs are signed | ✔ | ✔ | ✔ |

## Finding rules

- A finding = stable `CODE` (UPPER_SNAKE), severity (`info|low|medium|high`), string `params`.
- Wording lives only in `i18n/{en,es,fr}.json` → `findings.CODE.{title, shows, does_not_prove}` with `%{param}`
  placeholders; a test asserts the three catalogues have identical keys.
- `does_not_prove` is mandatory: courts and clients over-read findings. Examples: "identical hashes show identical
  content, not who copied the file"; "renaming is common and often innocent".
- Item status: `problem` if any high, `attention` if any medium/low, else `ok`. Any high finding ⇒ CLI exit 3.
- Run-level `limitations` codes (always printed): what the method cannot see (signature table limits, file-system
  timestamps, "no finding ≠ authentic", domain limits such as "carving cannot recover fragmented JPEGs").

## JSON schemas (in `schemas/`)

`rakvor.statistics/1`, `rakvor.item-technical/1`, `rakvor.item-summary/1`, `rakvor.item-custody/1` (JSON Schema 2020-12). Keys are
sorted (Rust serde_json maps; Python `sort_keys=True`) so both profiles produce the same layout. Adding a field
is minor; renaming/removing bumps `/N` and the schema file. `tests/validate_reports.py` validates a run.

## HTML

Templates `statistics.html`, `technical.html`, `summary.html` + `report.css` are valid for **both** minijinja
(Rust) and Jinja2 (Python): only `{{ }}`, `{% if %}`, `{% for %}`, `|safe` for the inlined CSS. Autoescape on.
Self-contained (CSS inlined, no external fonts or scripts) so they open offline in an evidence room.

## PDF

- Rust: built-in writer (`report/pdf.rs`): A4, Helvetica/Helvetica-Bold/Courier, WinAnsiEncoding (covers
  á é í ó ú ñ ü ç à è ê œ « » €), word wrap with hard breaks for hashes/paths, footer "page n of m", `/Info` metadata.
  Upgrade path: Typst or printpdf with embedded fonts for other scripts.
- Python: ReportLab platypus; build twice for "page n of m" with a **story factory** (flowables are single-use);
  an empty key/value table must render "—" (ReportLab rejects 0-row tables).
- Both render from the same view model as the HTML so they never disagree.
- Signing (when `--sign-cert`): every PDF incl. custody forms carries an AcroForm signature field and a detached
  CMS over its ByteRange (see `forensic-audit.md`). Check with `pyhanko sign validate` or Acrobat; with the test CA
  the signature is intact but the chain is untrusted — expected.

## Customer summary voice

Short sentences, no jargon, no blame. Status line (No issues found / Needs attention / Problem found), what we did,
what we found (one bullet per finding: title + "shows"), the fingerprint with one sentence explaining it, one
recommended next step. Same in ES/FR — write natural text, not word-for-word translation.
