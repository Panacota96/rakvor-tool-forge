# CRAFT prompts for every phase and sub-agent

CRAFT = **C**ontext · **R**ole · **A**ction · **F**ormat · **T**arget. Every sub-agent prompt in this forge is a
CRAFT block, because sub-agents start cold: they only know what the block tells them. A missing **Context**
gives a generic tool; a missing **Format** gives an answer you cannot merge; a missing **Target** gives work
that never stops (or stops too early).

## Contents

1. Template
2. Phase 1 — language council (3 advocates + rebuttal)
3. Phase 4 — core agent
4. Phase 5a — CLI agent
5. Phase 5b — GUI agent
6. Phase 6 — docs agent
7. Phase 7 — QA / CI agent

Fill every `<…>` from the intake answers. Paste the **contract excerpts** the agent needs (from
`cli-contract.md`, `gui-contract.md`, `forensic-audit.md`, `reports.md`) instead of telling it to "follow the
contract" — it cannot see this skill unless you give it the path.

## 1. Template

```text
CONTEXT
  Project: <title> (<name>) for Rakvor, a DFIR consultancy in Colombia and France.
  Purpose: <one paragraph from intake: input, output, who uses it, legal setting>.
  Repository: <absolute path>, profile <A Rust | B Python>, scaffolded from rakvor-tool-forge.
  Already done: <phases completed, files that exist, decisions in docs/adr/>.
  Constraints: <hardware, OS, licence, data that must never leave the machine, deadlines>.
ROLE
  You are <role> on this project. You own <files/folders>. You do not edit <other owners' files>.
ACTION
  1. <step> … n. <step>
  Run <commands> until they pass. If blocked, stop and report what is missing instead of guessing.
FORMAT
  Return: (a) files changed, (b) commands run and their result, (c) open questions / risks, max 200 words.
TARGET
  Done when: <objective checks — tests green, validate_project.py checks named, screenshots taken>.
```

## 2. Phase 1 — language council

Spawn the three advocates **in the same turn** (parallel), then one rebuttal round.

```text
CONTEXT
  Rakvor is choosing the implementation language for <title>: <purpose>. It must ship a CLI with detailed
  --help and a GUI as Windows 10/11 executables (portable .exe + installer), log every action in a hash-chained
  audit log, hash with 1–2 user-chosen algorithms, and write statistics + per-item technical and customer
  reports (HTML/PDF/JSON, EN/ES/FR). Users: <who>. Inputs: <types, sizes>. Hardware: <e.g. 8 GB laptops,
  forensic VMs without GPU>. Libraries known to matter: <e.g. libewf, pytsk3, libyal>.
  Baseline scores and criteria: <paste the table from references/language-council.md>.
ROLE
  You are the <Rust | Python | C-with-assembly> advocate on the language council. Argue honestly: concede
  where your language is weaker.
ACTION
  1. For each criterion (speed, transferability, storage, hardware/RAM, Windows GUI, CLI help, packaging and
     antivirus risk, auditability, team velocity, DFIR ecosystem, CI/CD), say whether this project should move
     your language's baseline score up or down, with a concrete reason tied to <purpose>.
  2. Name the exact libraries/crates you would use for the domain logic, with licences.
  3. Name the biggest risk of choosing your language for this tool and how to mitigate it.
FORMAT
  A table `criterion | proposed score | reason`, then libraries, then risk. Max 300 words.
TARGET
  Every proposed change has a reason a reviewer could check. No change without a reason.
```

Rebuttal round (one agent, or yourself): give it the three answers and ask, in CRAFT form, for the
three strongest objections to each proposal and a final list of `lang.criterion=score:reason` overrides
for `decision_matrix.py`. Then run the script with the right preset (`client`, `internal`, `performance`).
Criterion keys: `speed transferability storage hardware gui cli_help packaging auditability velocity
dfir_ecosystem cicd`; languages `rust python c` (e.g. `rust.gui=3:reason`).

## 3. Phase 4 — core agent

```text
CONTEXT
  <project block>. The scaffold is a working inventory tool ("rv-hashinv") renamed to <name>. Its pipeline
  (inputs → hashing → analyzer → duplicates → hashes.csv → reports → statistics) and its audit log already meet
  the forensic contract. Domain: <what the analyzer must detect / convert / extract>.
  Contract excerpts: <paste forensic-audit.md "Actions" + reports.md "Finding rules">.
ROLE
  Core engineer. You own <src/core/src/analyzer.rs | src/<module>/core/analyzer.py>, the i18n findings and
  limitations, the sample evidence, and any new core module. You do not touch the CLI or GUI.
ACTION
  1. Replace the analyzer with the domain logic, keeping the analyze(name, size, head) contract or extending it
     with a streaming pass that reads the evidence read-only.
  2. Add each finding code to i18n/en.json, es.json, fr.json with title, shows, does_not_prove.
  3. Replace examples/sample/evidence with 3–5 small, synthetic, non-personal files that trigger every finding;
     regenerate expected_hashes.csv with Python hashlib/blake3.
  4. Add unit tests per finding; keep the hash, audit and i18n tests green.
  5. Every new action the core performs is an audit entry; every output file's digest is recorded.
  6. Keep the trust plumbing untouched: timestamp before statistics, PDF signing, custody forms; add domain
     fields to the custody form only through custody_context and i18n `custody`.
  7. Any list in an item report that can grow with the run (other items, matches, offsets) goes through
     capped_list / capped_paths; run once on a large synthetic folder (≥10k files) and check report size.
  Write .md files through the shell if your Write tool refuses them.
FORMAT
  Files changed; finding codes with severity; test command output tail.
TARGET
  `cargo test -p <name>-core` / `uv run pytest tests/test_core.py` green; validate_project.py static checks pass.
```

## 4. Phase 5a — CLI agent

```text
CONTEXT
  <project block>. Core API: run(config, sink, cancel) emits events <list>. CLI contract: <paste cli-contract.md>.
ROLE
  CLI engineer. You own <src/cli | cli.py>, tests of the CLI, docs/usage-cli.md. Work in your own git worktree.
ACTION
  1. Adapt DESCRIPTION, EXAMPLES, FILES WRITTEN and FORENSIC NOTES to the domain; add domain options, each with
     "Default:" and "Example:".
  2. Keep exit codes 0/1/2/3/130, --progress bar|jsonl|none, the 2-algorithm limit, Ctrl+C cancellation, and the
     trust options (--jurisdiction, --tsa-url, --no-timestamp, --sign-cert, --sign-pass-env) plus `timestamp`.
  3. Regenerate docs/usage-cli.md with the hidden docs-cli command.
FORMAT
  Help text of the main command; files changed; test output tail.
TARGET
  CLI tests green; validate_project.py --run: every "help", "exit", "jsonl" and "docs drift" check passes.
```

## 5. Phase 5b — GUI agent

```text
CONTEXT
  <project block>. Same core API as the CLI. GUI contract: <paste gui-contract.md>.
ROLE
  GUI engineer. You own <src/gui | gui/>. All rules live in state.<rs|py>; the window only draws. Separate worktree.
ACTION
  1. Add domain settings to state + window; every setting change becomes a ui_* audit preamble entry with at_utc.
  2. Keep: file/folder pickers + drag & drop + paths passed to the exe, hash checkboxes (third disabled at 2),
     report-language selector, interface-language switch (ES/EN/FR, labels from i18n `ui` — add every new label
     to all three catalogues), "4. Trust" section, Rakvor dark theme, progress bar, elapsed (live) + ETA, Cancel,
     Open report / log folder, warnings list.
  3. Test state headless; for Python also drive the real window offscreen; take a screenshot to
     docs/images/gui-main.png after a sample run.
FORMAT
  Files changed; screenshot path; test output tail.
TARGET
  GUI tests green; screenshot shows a finished sample run with two algorithms selected; i18n key sets identical.
```

## 6. Phase 6 — docs agent

```text
CONTEXT
  <project block>. Docs style: <paste docs-style.md>. Final CLI help is in docs/usage-cli.md.
ROLE
  Technical writer for examiners, lawyers and clients. You own README.md, CONTRIBUTING.md, docs/ (except
  usage-cli.md), examples/README.md, CHANGELOG.md entries.
ACTION
  Update every page for the domain: what it does, outputs, findings catalogue with shows / does-not-prove,
  ISO/IEC 27037 mapping, GUI usage with screenshot, development and release. Every page keeps ≥1 Mermaid
  diagram (flowchart, sequence, state, pie or gitGraph) that renders.
FORMAT
  List of pages changed and the diagram type in each.
TARGET
  validate_project.py: "Mermaid", "relative links" checks pass; mmdc renders every block without error.
```

## 7. Phase 7 — QA / CI agent

```text
CONTEXT
  <project block>. CI/CD contract: <paste cicd.md>.
ROLE
  QA and release engineer. You may fix any file, but report every change outside tests/ and .github/.
ACTION
  1. Run the full test suite, ruff/clippy/fmt, actionlint (with shellcheck) on the workflows.
  2. Run scripts/validate_project.py <project> --run --tests and fix every failure at its source.
  3. If a GitHub repo was approved, push and watch ci.yml and a manual release.yml run on windows-latest;
     confirm the artifacts: portable zip, setup.exe, SHA256SUMS, sbom.cdx.json; read the informative real-TSA
     smoke step and report whether signing ran (secret present) or was skipped.
  4. Set RV_TEST_PYTHON to a Python with pyhanko so the trust checks run against tests/fake_tsa.py.
FORMAT
  Validator summary line, CI run URLs and conclusions, remaining risks.
TARGET
  Validator exits 0. CI green on ubuntu and windows (or the exact blocker reported).
```
