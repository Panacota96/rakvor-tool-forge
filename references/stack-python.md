# Profile B — Python

Template: `assets/template/python` (working project "rv-hashinv", 25 pytest tests incl. a real PySide6 window
driven offscreen; validator 50/50 on Linux).

## Layout

```text
pyproject.toml           hatchling; deps click, rich, blake3, jinja2, reportlab; extra [gui] PySide6-Essentials;
                         groups dev (pytest, jsonschema, ruff) and build (nuitka, ordered-set, zstandard)
uv.lock                  committed; CI uses `uv sync --locked`
src/<module>/
  __init__.py            TOOL_NAME, __version__  (# x-release-please-version)
  __main__.py            python -m <module>
  cli.py                 click group: scan, verify-log, self-test, hidden docs-cli
  core/                  timeutil, hashing, audit, events, inputs, analyzer, i18n, envinfo, report, run
  gui/                   state.py (no Qt), app.py (PySide6 MainWindow + QThread worker)
  resources/             templates/*.html + report.css, i18n/{en,es,fr}.json (package data)
tests/                   conftest (QT_QPA_PLATFORM=offscreen), test_core, test_cli (subprocess),
                         test_gui_state, test_gui_window, verify_chain.py, validate_reports.py
packaging/               build_windows.py (Nuitka standalone), entry/*.py, windows/installer.iss
```

## Library choices

| Purpose | Choice | Why |
| --- | --- | --- |
| CLI | click 8 (+ rich progress) | deterministic help text (`\b` blocks, fixed width) for the docs drift test; Typer's rich help panels depend on terminal width |
| Hashing | hashlib (`usedforsecurity=False` for md5/sha1) + blake3 | native speed, GIL released |
| HTML | Jinja2 (`select_autoescape(["html"])`) | same templates as minijinja |
| PDF | ReportLab platypus | standard fonts cover EN/ES/FR |
| GUI | PySide6-Essentials | LGPL; official Qt binding |
| Freeze | Nuitka `--standalone` (not onefile), `--enable-plugin=pyside6`, console mode force (CLI) / disable (GUI) | onefile unpacks to %TEMP% → slow + AV false positives |

## Domain work (core agent)

- Replace `core/analyzer.py`; keep `analyze(file_name, size, head) -> Analysis` and `Finding(code, severity, params)`.
- Heavy per-item work: stream with `fh.read(CHUNK)`, call the progress callback, check `cancel.is_set()` per chunk.
- DFIR libraries (pyewf, pytsk3, libyal bindings, regipy, python-evtx): pin in `pyproject.toml` (a runtime
  dependency lands in environment.json automatically: `envinfo.dependencies()` walks the installed requirement
  closure of the package, like the Rust side lists Cargo.lock), add them to `envinfo.TRACKED` too (fallback list
  and the metadata Nuitka embeds via `--include-distribution-metadata`), check their licences (GPL/LGPL
  implications for the Apache-2.0 project and for distribution).

## Gotchas met while building the reference

- `event(kind, **fields)` collided with a `kind=` field → make the first parameter positional-only (`/`).
- ReportLab flowables are single-use: build the story twice via a factory for "page n of m".
- `Table([])` raises — render "—" for empty key/value tables (cancelled runs).
- `json.dumps(sort_keys=True)` for report JSON to match the Rust layout.
- Linux CI needs Qt runtime libs (`libegl1 libxkbcommon0 libgl1 libfontconfig1 libdbus-1-3`); xcb also needs
  `libxcb-cursor0` — use `offscreen` instead.

## Trust stack (timestamp + PDF signing)

- `pyhanko` signs PDFs (`signers.SimpleSigner.load_pkcs12`) after ReportLab writes them; `asn1crypto` (pulled in
  by pyhanko) builds and parses the RFC 3161 request/response; transport is plain `urllib` (http/https only),
  reading at most `MAX_RESPONSE` = 1 MiB (larger → `TimestampError`, same limit as ureq in the Rust profile).
  environment.json records `tool.openssl` (Python's `ssl`/`hashlib` and `cryptography`'s bundled OpenSSL).
- `core/trust.py` mirrors the Rust module: `build_request`, `parse_response`, `timestamp_head`,
  `verify_timestamps`, `PdfSigner`. `AuditLog.resume` powers the `timestamp` command.
- click: `--no-timestamp` with `envvar="RV_NO_TIMESTAMP"`; password via `--sign-pass-env` or `click.prompt(hide_input=True)`.
- Nuitka: pyhanko and asn1crypto freeze without plugins; keep the frozen-CLI smoke test, which exercises them.
- Tests: `tests/fake_tsa.py` (shared with Profile A) starts a local TSA; `conftest.py` sets `RV_NO_TIMESTAMP=1`
  for every other test; ruff per-file-ignores S105/S106 for test passwords.

## Nuitka and package metadata (learned)

`--include-distribution-metadata=X` fails the build ("including metadata for distribution 'X' without including
related package") when X's package is not imported by that entry point — e.g. PySide6 in the CLI build, click in the
GUI build, or the project's own editable install. Embed per front-end (shared runtime deps in both, CLI-only and
GUI-only sets separately) and let `envinfo` fall back to the TRACKED list when the project's own metadata is absent.
Test locally on Linux with `uv pip install patchelf` before pushing; the Windows job takes ~20 minutes.
