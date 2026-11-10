# CLI contract

Every Rakvor CLI behaves the same way, so examiners learn it once and scripts keep working across tools.

## Commands

| Command | Purpose | Notes |
| --- | --- | --- |
| `<tool> scan` | Main work (rename the verb if the domain needs it: `convert`, `carve`; record it in `.forge.json` `run_command`) | All common options below |
| `<tool> verify-log <LOG>` | Verify an `audit.jsonl` (or its folder): chain, timestamp tokens, truncation against the timestamp's `covers_seq` and the case log's `run_finished` (see forensic-audit.md) | exit 0 intact, 3 broken or truncated, 1 unreadable |
| `<tool> verify-case <CASE> [--rehash]` | Verify a case folder; `--rehash` re-hashes reachable registered evidence (see case-folder.md) | exit 0 intact, 3 broken or evidence differs, 1 not a case folder |
| `<tool> self-test` | Known-answer test of every hash algorithm (input `"abc"`) | exit 0 / 1 |
| `<tool> docs-cli` | **Hidden.** Prints every command's long help as Markdown → `docs/usage-cli.md` | CI fails on drift |

## Common options (main command)

| Option | Values | Default |
| --- | --- | --- |
| `-i, --input PATH` (repeatable) | file or folder | — (required unless `--input-list`) |
| `-L, --input-list FILE` | text file (UTF-8, a leading BOM is ignored), one path per line, `#` comments, paths relative to the list | — |
| `-r, --recursive` | flag | off |
| `-H, --hash ALGO[,ALGO]` | 1–2 of `md5 sha1 sha256 sha512 sha3-256 blake3` | `sha256` |
| `-o, --out DIR` | output root (`logs/` and `reports/` inside) | `./rakvor-out` |
| `-l, --lang LANG` | `en`, `es`, `fr`, `all` (comma lists allowed) | `en` |
| `-a, --actor NAME` | examiner name | OS user name |
| `-c, --case ID` | case / engagement id | — |
| `-p, --progress MODE` | `bar` (stderr: bar, bytes, rate, elapsed, ETA), `jsonl` (events on stdout), `none` | `bar` |
| `-j, --jurisdiction co\|fr` | custody form: Colombia (ES) or France (FR), English always added | `co` |
| `--tsa-url URL` | RFC 3161 TSA (http/https only) | `http://timestamp.digicert.com` |
| `--no-timestamp` | skip the trusted timestamp (env `RV_NO_TIMESTAMP=1`; logged) | off |
| `--sign-cert FILE` | examiner `.p12`/`.pfx`; signs every PDF | — (unsigned) |
| `--sign-pass-env VAR` | env var holding the certificate password; prompt **without echo** if unset (rpassword / click `hide_input`) | `RV_SIGN_PASSWORD` |

Plus `<tool> timestamp <LOG>`: re-verifies the chain and appends a trusted timestamp to a run that was offline
(exit 0 obtained, 1 TSA still unreachable, 3 log missing or chain broken — never a traceback). The `scan` summary line prints `timestamp: ok|pending|off`
and `verify-log` prints `timestamp … covers entry #N` for every token.

Item numbering (both profiles, identical): every input path is made absolute and lexically normalised, duplicates
are dropped, and items are sorted by that string **bytewise (UTF-8)**; item 1 is the smallest path. Never sort by
`PathBuf` components or locale.

Domain options are added after these, with the same documentation rules.

## `--help` anatomy

`-h` gives a short summary; `--help` gives the full page with, in this order:

1. **DESCRIPTION:** what it does to each file, numbered steps, where outputs go.
2. **Usage / Options:** every option documents what it does, then a line `Default: …` and a line `Example: …`
   (validator counts both). Use `verbatim_doc_comment` (clap) or click's `\b` blocks so lines are not re-wrapped.
3. **EXAMPLES:** single file, bulk folder with two hashes and Spanish reports, list file with `--lang all --progress jsonl`, verify afterwards.
4. **EXIT CODES:** table below.
5. **FILES WRITTEN:** every file under `<OUT>/logs/<RUN-ID>/` and `<OUT>/reports/<RUN-ID>/`.
6. **FORENSIC NOTES:** read-only access, work on verified copies, chain semantics, "no finding ≠ authentic",
   MD5/SHA-1 only paired with a strong algorithm.

Keep the help width fixed (100 columns: clap `max_term_width`/`term_width`, click `max_content_width`) so
`docs-cli` output is identical on every machine.

## Exit codes

| Code | Meaning |
| :-: | --- |
| 0 | Completed; no high-severity finding |
| 1 | Runtime error (input missing, output not writable) — still audited when a log exists |
| 2 | Usage error: unknown option, **more than two algorithms**, unknown language/algorithm |
| 3 | Completed with ≥1 high-severity finding, or `verify-log` / `verify-case` found a broken or truncated chain (or, with `--rehash`, evidence that differs) |
| 130 | Cancelled by Ctrl+C: partial reports written, `run_cancelled` logged |

## Events (`--progress jsonl`)

One compact JSON object per line on stdout; identical field names in Rust and Python:

```json
{"event":"run_started","run_id":"20261019T204208Z-809cfe","tool":"rv-x","version":"0.1.0","frontend":"cli","algorithms":["sha256","blake3"],"langs":["en"],"total_items":3,"total_bytes":234}
{"event":"item_started","index":1,"path":"/abs/path/a.txt","size":61}
{"event":"progress","done":0,"total":3,"bytes_done":61,"bytes_total":234,"elapsed_ms":1,"eta_ms":null}
{"event":"item_hashed","index":1,"path":"/abs/path/a.txt","hashes":{"blake3":"…","sha256":"…"}}
{"event":"item_finished","index":1,"path":"/abs/path/a.txt","status":"ok","findings":[]}
{"event":"report_written","kind":"statistics","path":"…/statistics.en.html"}
{"event":"run_finished","items":3,"bytes":234,"errors":0,"elapsed_ms":42,"high":0,"medium":1,"low":0,"info":2,"report_dir":"…","log_dir":"…","cancelled":false}
```

Also `error {path, message}` and `cancelled {done, elapsed_ms}`. `progress` is throttled (~150 ms) and always
emitted after each item; `elapsed_ms` is monotonic; `eta_ms` is `null` until 500 ms of data exist.

## Robustness rules

- A closed stdout (`| head`) must never abort a run: stop printing, finish the work, keep the log complete.
- Ctrl+C sets the cancel flag; the core checks it after every 1 MiB chunk.
- Never print from the core; the CLI only renders events and the final summary.
