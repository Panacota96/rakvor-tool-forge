# Profile A — Rust workspace

Template: `assets/template/rust` (working project "rv-hashinv", verified on Linux and on GitHub `windows-latest`).

## Layout

```text
Cargo.toml                 workspace (resolver 3, edition 2024, rust-version 1.88), shared deps, release profile;
                           member crates set `publish.workspace = true` (cargo-deny treats the rest as public)
deny.toml                  cargo-deny: licence allow-list (no GPL/AGPL/LGPL), sources, RustSec advisories with
                           documented exceptions only
.cargo/config.toml         +crt-static for *-pc-windows-msvc (runs on a clean Windows without VC++ redistributable)
src/core/                  <name>-core library (lib name <module>_core)
  build.rs                 embeds Cargo.lock dependency versions, rustc version, target → environment.json
  src/{lib,hash,audit,events,input,analyzer,i18n,run,env,time}.rs, src/report/{mod,pdf}.rs
src/cli/                   <name>-cli, binary <name>; tests/cli.rs (assert_cmd)
src/gui/                   <name>-gui, binary <name>-gui; src/{lib,state,app,main}.rs
templates/ i18n/ schemas/  shared assets (include_str! from core)
```

## Crates (versions verified October 2026)

| Purpose | Crate | Notes |
| --- | --- | --- |
| Hashing | md-5 0.11, sha1 0.11, sha2 0.11, sha3 0.12, digest 0.11, blake3 1.8 | one `Hasher` enum, one read loop |
| JSON | serde 1, serde_json 1 | **no** `preserve_order` (canonical sorted keys) |
| HTML | minijinja 3 with feature `serde` | context via `minijinja::value::Serde(&ctx)` |
| Time | chrono 0.4 (default-features off; clock, std) | |
| Walk | walkdir 2.5 (`follow_links(false)`) | |
| CLI | clap 4.6 (derive, wrap_help), indicatif 0.18, ctrlc 3.5 | `verbatim_doc_comment`, `hide_default_value`, `max_term_width = 100` |
| GUI | eframe 0.37 (default-features off; default_fonts, wgpu, x11, wayland), rfd 0.17 Windows-only | `App::ui()` API; `egui::Panel::top` |
| Tests | tempfile 3, assert_cmd 2, predicates 3 | |

## Release profile

`lto = "thin"`, `codegen-units = 1`, keep symbols, **no UPX** (packers trigger antivirus heuristics). Build both
binaries with `cargo build --release --locked -p <name>-cli -p <name>-gui`.

## Domain work (core agent)

- Replace `analyzer.rs` (signature-based inventory) with the domain analysis. Keep `analyze(name, size, head)` for
  per-item checks; add a streaming pass (`BufReader` 1 MiB, cancel check per chunk, progress via the sink) for
  heavy work like carving, writing outputs under `<OUT>/…/<RUN-ID>/` and hashing every output.
- Prefer maintained pure-Rust crates; wrap C libraries (libewf, libtsk) with `cc`/`bindgen` only when needed
  (see `stack-hybrid.md`).
- Large images: memory-map only read-only (`memmap2::Mmap` on `File::open`), or stream; never load whole files.
- SIMD: rely on crates (blake3, memchr). Hand-written assembly is not allowed; `std::arch` intrinsics only after
  profiling, behind a portable fallback and tests comparing both paths.

## Gotchas met while building the reference

- `cargo run … | head` panicked on broken pipe mid-run → write with `writeln!` and stop printing on error.
- `std::path::absolute` (not `canonicalize`) to avoid `\\?\` prefixes on Windows.
- Clippy `write_with_newline` in PDF xref → `writeln!`.
- Default egui fonts lack ▶ ■ ✖ glyphs.
- `.gitattributes` `* text=auto eol=lf` so `docs/usage-cli.md` and hashes match on Windows checkouts.

## Trust stack (timestamp + PDF signing)

| Need | Crates | Notes |
| --- | --- | --- |
| RFC 3161 client | `ureq 3`, `x509-tsp 0.1` (request types), own lenient TSTInfo DER walk | `genTime` from real TSAs may carry fractional seconds; strict `GeneralizedTime` rejects it |
| Examiner certificate | `p12-keystore 0.4`, `x509-cert 0.2`, `der 0.7`, `spki 0.7`, `const-oid 0.9` | RSA keys only — document it; ECDSA needs `p256`/`p384` |
| CMS signature | `cms 0.2` (SignedDataBuilder), `rsa 0.9`, `sha2 0.10` (renamed `sha2-legacy` next to the newer sha2 used for hashing) | detached over the PDF ByteRange |
| PDF placeholder | own writer: AcroForm + `/Sig` field + widget, `/Contents` of 16384 bytes, ByteRange patched in place | test parses ByteRange and re-verifies |
| CLI env options | clap feature `env`; bool flags from env need `FalseyValueParser` | `RV_NO_TIMESTAMP=1` |

`src/core/src/trust.rs` holds all of it; `AuditLog::resume` powers the `timestamp` command. Avoid `gen` as an
identifier (reserved in edition 2024).

## When a dependency needs a newer compiler

The pinned toolchain is per repository. If a crate needs a newer `rustc` (rv-mailauth: `mail-parser` 1.0, pulled in
by `mail-auth`, needs 1.98), bump `rust-toolchain.toml` and `rust-version` in that repo deliberately, in its own
`build:` commit, run clippy and the tests on the new compiler (lints change), and say so in the PR. Prefer a crate's
pure-Rust feature over a C build (`mail-auth`: `rust-crypto` instead of the default `aws-lc-rs`), and run
`cargo deny check` on the new tree before adopting it.
