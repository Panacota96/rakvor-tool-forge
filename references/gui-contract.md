# GUI contract (Windows 10/11)

The GUI is a second front-end on the same core: it builds a `RunConfig`, runs the core on a worker thread and
renders events. Any rule (max 2 algorithms, when Start is enabled, what is audited) lives in a **state module**
with unit tests; the window file only draws.

## Window layout

```text
┌ [RV] RAKVOR · <Title> vX.Y.Z   FORENSICS | INCIDENT RESPONSE | SECURITY   Interface [Español ▾] ┐
│ 1. Evidence   [Add files…] [Add folder…] [ …or type / paste a path ] [Add / Remove]          │
│               list of inputs (drag & drop onto window; paths passed to the .exe are preloaded)│
│               [x] Include subfolders                                                          │
│ 2. Hash algorithms (choose 1 or 2)   [ ] md5 [ ] sha1 [x] sha256 [ ] sha512 [ ] sha3-256 [x] blake3│
│ 3. Reports    Language [English ▾]  Output folder [rakvor-out] [Browse…]  Examiner  Case ID  │
│ 4. Trust      [x] Trusted timestamp  TSA [http://timestamp.digicert.com]                      │
│               Certificate [examiner.p12] [Certificate…] Password [••••]  Jurisdiction [Colombia ▾]│
│ <domain settings>                                                                              │
│ [Start review] [Cancel]                                                                        │
│ ███████████████████░░░░░░  2 / 3 files · 66%                                                   │
│ Elapsed: 4 min 05 s · Remaining (estimate): 1 min 10 s          Now: D:\evidence\img001.dd    │
│ Done — 3 files · 0 errors · findings: 0 high, 1 medium …   [Open report] [Open log folder]   │
│ Warnings (n)                                                                                   │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
```

## Rules (implemented in `state`)

| Rule | Behaviour |
| --- | --- |
| Hash selection | Exactly 1–2 ticked. When 2 are ticked the others are disabled; the last one cannot be unticked. |
| Start | Enabled only when idle, ≥1 input and an output folder. |
| While running | All settings disabled; Start becomes Cancel. |
| Elapsed time | Updated from the window clock every 200 ms (keeps moving on huge files), never less than the last event's `elapsed_ms`. |
| ETA | From `progress.eta_ms`; shows "—" when unknown or idle. |
| Progress fraction | Bytes done / bytes total; falls back to items when all files are empty. |
| Finish | Shows counts by severity, **Open report** (statistics in the chosen language; English when "all") and **Open log folder**. |
| Interface language | ES/EN/FR selector in the header; every label comes from i18n `ui` (`state.tr(key)`), switch applies immediately; independent of the report language. Default: OS locale, else English. |
| Trust | Timestamp on by default; certificate picker + password field (password kept in memory only, never in settings, logs or environment.json); jurisdiction Colombia/France. A wrong password is reported when Start is pressed. |
| Errors | Run errors → warnings list (also `item_error` in the log). Validation errors before a run → message box (not logged: no run exists yet). |

## Audited user actions

Recorded with `at_utc` when they happen, written to the audit log as preamble entries right after
`run_started` (the log does not exist before Start):

`ui_input_added`, `ui_input_removed`, `ui_hash_selection_changed`, `ui_language_changed`,
`ui_recursive_changed`, `ui_interface_language_changed`, `ui_timestamp_changed`, `ui_signing_certificate_changed`
(file name + fingerprint only), `ui_jurisdiction_changed`, `ui_<domain>_changed`, `ui_start_clicked` (final settings). Cancel is logged by the core
as `item_cancelled` + `run_cancelled` with `frontend: "gui"`. `environment.json` stores the GUI settings as its
"command line".

## Brand

Dark theme from the Rakvor palette (`#0D1117` background, `#1A1F26` panels, `#2A5CFF`→`#6B00FF` accents, `#00E5FF`
progress, `#E6E6E6` text), RV mark in the header, window/exe icon from `rv.ico`. Rust: `apply_theme(ctx)` sets
`egui::Visuals`; Python: one Qt stylesheet. Keep contrast ≥ 4.5:1 for text.

## Profile A (Rust, eframe/egui 0.37)

- `App::ui(&mut self, ui, frame)` (0.37 API; `logic()` for non-drawing work); panels via `egui::Panel::top(..).show(ui, ..)`
  and `egui::CentralPanel::default().show(ui, ..)`.
- `Renderer::Wgpu`: falls back to WARP software rendering on Windows VMs without GPU drivers.
- `#![cfg_attr(windows, windows_subsystem = "windows")]` — no console window.
- Native pickers with `rfd` as a **Windows-only** dependency (no GTK/portal needed on Linux CI); a path text field
  and drag & drop work everywhere.
- Default fonts lack some symbols (▶ ■ ✖ render as boxes): use plain words on buttons.
- Worker: `std::thread` + `mpsc` channel; the sink calls `ctx.request_repaint()`.
- Test: `state.rs` unit tests, plus one test that runs the real core from GUI settings. For screenshots on Linux:
  Xvfb + `WGPU_BACKEND=gl`, start the exe with the sample folder as argument, drive clicks with XTest.

## Profile B (Python, PySide6)

- `PySide6-Essentials` only (smaller; LGPL-3.0 — ship as DLLs, never statically).
- Worker: `QObject` moved to a `QThread`; events cross threads as a `Signal(dict)`; a `QTimer` (200 ms) refreshes
  the elapsed label.
- Drag & drop: `setAcceptDrops(True)`, `dragEnterEvent`/`dropEvent` with local file URLs.
- Tests: `QT_QPA_PLATFORM=offscreen` (set in `tests/conftest.py`); drive the real `MainWindow`
  (click checkboxes, Start, wait for `phase == "finished"`), save `w.grab()` as the docs screenshot.
- Linux CI needs `libegl1 libxkbcommon0 libgl1 libfontconfig1 libdbus-1-3`.
