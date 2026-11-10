# Language council

Pick the language per project, not per habit. Three advocates (Rust, Python, C-with-assembly) argue from the
same baseline, a rebuttal round challenges them, and `scripts/decision_matrix.py` turns the result into an ADR.

## Criteria (what each one means for a DFIR tool)

| Criterion | Ask |
| --- | --- |
| Speed | Throughput on multi-GB images; startup time for many small runs. Hashing is usually I/O-bound. |
| Transferability | Runs on Windows 10 and 11 (x64, ARM64) and Linux from one code base without a runtime install? |
| Storage | Size of the portable zip / installer (USB sticks, air-gapped labs). |
| Hardware / RAM | Works on 8 GB laptops and forensic VMs without GPU; streams instead of loading files. |
| Windows GUI | Mature, accessible, native-looking GUI toolkit with file pickers and drag & drop. |
| CLI help | Rich `--help` with sections, defaults and examples; generated docs. |
| Packaging / AV | Signing, SmartScreen reputation, antivirus false positives (packers, onefile unpackers). |
| Auditability | Memory safety, reproducible builds, how easy it is to explain the code to a court expert. |
| Team velocity | How fast the team (Python-strong, learning Rust) can change it. |
| DFIR ecosystem | Libraries for the domain (libyal, libewf, pytsk3, dfvfs, regipy, …). |
| CI/CD | How simple it is to build, test and release on GitHub Actions for Windows. |

## Baseline scores (October 2026)

| Criterion | Rust | Python | C (+asm) | Notes |
| --- | :-: | :-: | :-: | --- |
| Speed | 5 | 3 | 5 | Python's hashlib/blake3 are native; Python loops are not |
| Transferability | 5 | 4 | 3 | Rust static CRT; Python via Nuitka; C needs per-OS code |
| Storage | 4 | 2 | 5 | Rust ~5–15 MB; Python+Qt 60–90 MB |
| Hardware / RAM | 5 | 3 | 5 | egui wgpu falls back to WARP software rendering |
| Windows GUI | 4 | 4 | 2 | egui/eframe vs PySide6 (Qt) vs raw Win32 |
| CLI help | 5 | 5 | 2 | clap / click vs getopt |
| Packaging / AV | 4 | 2 | 4 | Python freezers trigger more AV heuristics |
| Auditability | 5 | 3 | 2 | memory safety, lockfiles |
| Team velocity | 3 | 5 | 3 | |
| DFIR ecosystem | 4 | 5 | 4 | Python has the widest bindings; C has the original libraries |
| CI/CD | 5 | 3 | 3 | |

Assembly: never hand-written in a deliverable. Hash and parsing crates already use SIMD/CPU intrinsics; hand-written
NASM breaks across calling conventions (the old rv-core used the System V ABI and crashed on Windows) and is
hard to defend in court. Allow intrinsics in Rust (`std::arch`) only after profiling proves a hot loop.

Ruby: internal scripts only (no mature Windows GUI, weak packaging).

## Weight presets (`decision_matrix.py --preset …`)

| Criterion | client | internal | performance |
| --- | :-: | :-: | :-: |
| Speed | 3 | 1 | 4 |
| Transferability | 3 | 1 | 2 |
| Storage | 2 | 1 | 1 |
| Hardware / RAM | 2 | 1 | 4 |
| Windows GUI | 2 | 2 | 1 |
| CLI help | 2 | 2 | 1 |
| Packaging / AV | 2 | 1 | 1 |
| Auditability | 3 | 1 | 3 |
| Team velocity | 2 | 4 | 1 |
| DFIR ecosystem | 0 | 4 | 1 |
| CI/CD | 1 | 0 | 1 |

- **client** — delivered to clients or used in court. Baseline result: Rust 100, Python 75, C 75 (max 110).
- **internal** — examiner tooling that changes often. Baseline: Python 75, Rust 74, C 60 (max 90) — a close call.
- **performance** — very large inputs on modest hardware (carving, image parsing).

## Decision rules

1. Run the preset that matches the tool, apply the council's overrides (each with a reason).
2. Within 3 points → tie-breaker: Rust for client/performance, Python for internal.
3. C never wins on its own (no supported GUI/CLI profile): it is used only to wrap an existing C library
   (Rust `cc`/`bindgen`, Python `ctypes`/cffi) — see `stack-hybrid.md`.
4. If the domain needs a Python-only library but the tool goes to clients, consider Profile C (Rust core
   + Python via PyO3) or a Python core frozen with Nuitka; write the trade-off in the ADR.
5. The ADR (`docs/adr/0001-language.md`) is part of the plan the user approves in phase 2.

## Reading the council

Good overrides are concrete: "python.dfir_ecosystem stays 5: pyevtx/libyal parse EVTX; no maintained Rust crate
covers corrupted chunks" — checkable. Bad: "Rust is faster" — already in the baseline. Reject overrides
without a reason, and cap each language to three overrides so the matrix stays explainable.
