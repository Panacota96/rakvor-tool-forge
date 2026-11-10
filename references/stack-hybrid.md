# Profile C — hybrids (documented, not templated)

Use a hybrid only when the council shows a concrete need; each language boundary adds build, packaging and
audit complexity. Record the boundary in the ADR.

## C1 — Rust core, Python front-ends or plugins (PyO3 + maturin)

When: the tool goes to clients (Rust core for auditability/speed) but analysts want Python notebooks or plugins.

```text
src/core            Rust library (unchanged from Profile A)
src/py              crate-type = ["cdylib"], pyo3 = { features = ["extension-module", "abi3-py311"] }
                    #[pyfunction] fn run(config_json: &str, callback: PyObject) -> PyResult<String>
pyproject.toml      build-backend = "maturin"
```

- Pass config/results as JSON strings across the boundary (no shared structs to keep in sync).
- Release the GIL (`py.allow_threads`) while hashing; call back into Python only to emit events.
- CI: `PyO3/maturin-action` builds abi3 wheels for windows-latest and ubuntu.

## C2 — Python or Rust core wrapping a C library

When: the best implementation of a format exists only in C (libewf for E01, libtsk for file systems, libyal family).

| From | How | Notes |
| --- | --- | --- |
| Rust | `cc` crate to compile vendored C, `bindgen` for headers, a thin `unsafe` module with safe wrappers | static link; check licence (LGPL-3 libyal → dynamic linking or source offer) |
| Python | existing bindings (pyewf, pytsk3) first; else `ctypes`/cffi | ship the DLL next to the Nuitka build |

Rules: no assembly; build the C code with the target's default calling convention (MSVC on Windows — the old
rv-core NASM code assumed System V and crashed); fuzz the wrapper with the sample corpus; record the C library
version in environment.json.

## C3 — Rust CLI/GUI calling a Python worker process

Rarely worth it: two runtimes to ship. Only for a temporary bridge while porting a Python analyzer to Rust.
Communicate with JSON Lines over stdin/stdout using the same event schema.
